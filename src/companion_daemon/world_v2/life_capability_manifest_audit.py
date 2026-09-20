"""Exact internal capability sidecars required for successful Life recovery.

An audit binding establishes stored bytes only. Existing request, proposal and
World checks remain the authority for using those capabilities. Missing bytes
are never reconstructed from metadata or a later manifest.
"""

from typing import Literal

from pydantic import Field

from .life_content_store import (
    MAX_CAPABILITY_MANIFEST_AUDIT_UTF8_BYTES,
    ImmutableLifeContentStore, StoredLifeContent, life_content_payload_hash,
)
from .life_development_draft import LifeDevelopmentCapabilityManifest
from .proposal_audit_schemas import canonical_json
from .schema_core import FrozenModel


class _LegacyBinding(FrozenModel):
    content_ref: str = Field(min_length=1, max_length=512)
    content_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CapabilityManifestAuditBinding(_LegacyBinding):
    contract: Literal["life-capability-manifest-audit.1"] = "life-capability-manifest-audit.1"
    content_kind: Literal["capability_manifest_audit"] = "capability_manifest_audit"
    utf8_bytes: int = Field(ge=1, le=MAX_CAPABILITY_MANIFEST_AUDIT_UTF8_BYTES)


def validate_capability_manifest_binding(binding: dict) -> _LegacyBinding | CapabilityManifestAuditBinding:
    if not isinstance(binding, dict):
        raise ValueError("recoverable capability manifest binding is invalid")
    model = CapabilityManifestAuditBinding if "contract" in binding else _LegacyBinding
    if set(binding) != set(model.model_fields):
        raise ValueError("recoverable capability manifest binding shape is invalid")
    return model.model_validate(binding, strict=True)


def record_capability_manifest_audit(
    *, content_store: ImmutableLifeContentStore, content_ref: str,
    manifest: LifeDevelopmentCapabilityManifest,
) -> CapabilityManifestAuditBinding:
    text = canonical_json(manifest.model_dump(mode="json", exclude_computed_fields=True))
    binding = CapabilityManifestAuditBinding(
        content_ref=content_ref, content_payload_hash=life_content_payload_hash(text),
        utf8_bytes=len(text.encode("utf-8")),
    )
    content_store.put_if_absent(StoredLifeContent(
        content_ref=content_ref, content_kind=binding.content_kind,
        content_payload_hash=binding.content_payload_hash, text=text,
    ))
    # Adapters may retain identical bytes in an older kind. Read back the exact
    # kind too before the caller may commit a recoverable success audit.
    read_capability_manifest_audit(content_store=content_store, binding=binding.model_dump(mode="json"))
    return binding


def read_capability_manifest_audit(
    *, content_store: ImmutableLifeContentStore, binding: dict,
) -> tuple[StoredLifeContent, LifeDevelopmentCapabilityManifest]:
    parsed = validate_capability_manifest_binding(binding)
    kind = parsed.content_kind if isinstance(parsed, CapabilityManifestAuditBinding) else "outcome_candidate"
    stored = content_store.read_exact(content_ref=parsed.content_ref)
    if (
        stored is None or stored.content_kind != kind
        or stored.content_payload_hash != parsed.content_payload_hash
        or life_content_payload_hash(stored.text) != parsed.content_payload_hash
        or (isinstance(parsed, CapabilityManifestAuditBinding)
            and len(stored.text.encode("utf-8")) != parsed.utf8_bytes)
    ):
        raise ValueError("recoverable capability manifest sidecar is unavailable")
    return stored, LifeDevelopmentCapabilityManifest.model_validate_json(stored.text)
