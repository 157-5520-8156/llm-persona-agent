"""Resolve new consequence review evidence from the original committed author audit."""

from __future__ import annotations

import json

from .life_content_store import life_content_payload_hash
from .life_development_draft import parse_world_author_draft
from .proposal_audit_schemas import RecordedModelResultAudit, canonical_json
from .world_author_request_audit import WorldAuthorRequestBinding, read_world_author_request
from .world_consequence_contract import validate_world_consequence_authority


def read_world_consequence_author_evidence(
    *, ledger, content_store, manifest, actor_ref: str, draft, raw: str,
    author_deliberation: dict[str, object],
) -> dict[str, object]:
    """Verify durable model/Proposal lineage before using request bytes as evidence."""
    from .reducers import _validate_one_life_development_deliberation
    from .world_consequence_authoring_context import (
        WorldConsequenceAuthoringContext, validate_world_consequence_authoring_context,
    )

    if manifest.outcome_contract != "world-consequence.2":
        raise ValueError("world consequence evidence requires an explicit current manifest")
    state = ledger.project()
    binding = _validate_one_life_development_deliberation(
        state,
        proposal={
            "world_author_deliberation": author_deliberation,
            "world_author_deliberation_hash": life_content_payload_hash(canonical_json(author_deliberation)),
            "evaluated_world_revision": manifest.pinned_cursor.world_revision,
        },
        field="world_author_deliberation", expected_role="world_author",
    )
    if binding["capability_manifest"] != manifest.model_dump(mode="json", exclude_computed_fields=True):
        raise ValueError("world consequence evidence changed the original manifest")
    model = next(item for item in state.model_result_audits
                 if item.model_result_ref == binding["final_model_result_ref"])
    audit = RecordedModelResultAudit.model_validate_json(model.audit_json)
    if life_content_payload_hash(raw) != audit.response_hash:
        raise ValueError("world consequence draft changed the original author response")
    messages = read_world_author_request(
        content_store=content_store,
        binding=WorldAuthorRequestBinding.model_validate(binding["request_bindings"][-1]),
        expected_request_hash=audit.request_hash,
    )
    declarations = []
    for message in messages:
        if message["role"] != "user":
            continue
        try:
            value = json.loads(message["content"])
        except ValueError:
            continue
        if isinstance(value, dict) and "execution_authority" in value:
            declarations.append({
                "authority": value["execution_authority"],
                "execution_materials": value.get("execution_materials"),
            })
    if len(declarations) != 1:
        raise ValueError("world consequence original request has no unique execution context")
    context = WorldConsequenceAuthoringContext.model_validate_json(canonical_json(declarations[0]))
    validate_world_consequence_authoring_context(
        ledger=ledger, content_store=content_store, manifest=manifest, actor_ref=actor_ref,
        authority=context.authority, execution_materials=context.execution_materials,
    )
    pinned = ledger.project_at(manifest.pinned_cursor)
    if parse_world_author_draft(raw=raw, manifest=manifest, logical_time=pinned.logical_time) != draft:
        raise ValueError("world consequence parsed draft changed its original author bytes")
    events = tuple(ledger.lookup_event_commit(item.source_event_ref)[0]
                   for item in context.authority.execution_bindings)
    for outcome in draft.outcomes:
        if outcome.world_consequence is None:
            raise ValueError("world consequence evidence cannot upgrade historical text")
        validate_world_consequence_authority(
            consequence=outcome.world_consequence, authority=context.authority,
            pinned_state=pinned, source_events=events, author_messages=messages, author_audit=audit,
        )
    return context.model_dump(mode="json")
