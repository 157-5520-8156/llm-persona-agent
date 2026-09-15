"""Exact rejected Life output for one same-author correction, never evidence.

This module owns only transport identity and bounds. It does not judge prose,
approve proposals, select a replacement, or reinterpret a failure as null.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from ..schema_core import FrozenModel

MAX_REJECTED_RESULT_BYTES = 131_072
CORRECTION_FIELDS = frozenset({
    "correction_ordinal", "correction_failure_code", "correction_failure_detail",
    "correction_rejected_expression", "correction_rejected_role_result",
})


def original_role_request(request):
    return request.model_copy(update={
        "correction_ordinal": 0,
        **{name: None for name in CORRECTION_FIELDS if name != "correction_ordinal"},
    })


def role_request_binding(request) -> str:
    # Include the whole pinned request (snapshot, subject, capabilities, recall
    # state), not just its cursor. The failure detail is new correction data.
    value = request.model_dump(mode="json", exclude=CORRECTION_FIELDS)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class RejectedRoleResult(FrozenModel):
    contract: Literal["rejected-life-role-result.1"] = "rejected-life-role-result.1"
    authority: Literal["rejected_candidate_not_world_evidence"] = "rejected_candidate_not_world_evidence"
    purpose: Literal["world_stimulus_appraisal"] = "world_stimulus_appraisal"
    request_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    response_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_call_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=256)
    model_version: str = Field(min_length=1, max_length=256)
    raw_result: str = Field(min_length=1, max_length=MAX_REJECTED_RESULT_BYTES)

    @model_validator(mode="after")
    def exact_bounded_bytes(self):
        raw = self.raw_result.encode("utf-8")
        if len(raw) > MAX_REJECTED_RESULT_BYTES:
            raise ValueError("rejected role result exceeds the byte bound; do not truncate")
        if self.response_hash != "sha256:" + hashlib.sha256(raw).hexdigest():
            raise ValueError("rejected role result differs from its original response hash")
        return self

    def verify_request(self, request):
        # model_copy can bypass Pydantic validation; revalidate at consumption.
        checked = type(self).model_validate_json(self.model_dump_json())
        if (
            request.correction_ordinal != 1
            or request.purpose != checked.purpose
            or checked.request_binding_sha256 != role_request_binding(request)
        ):
            raise ValueError("rejected role result belongs to a different pinned request")
        return checked
