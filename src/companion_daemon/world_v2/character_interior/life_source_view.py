"""Bind a Life source catalogue to the exact sent snapshot presentation.

This is inspectable preparation, not a semantic review or write permission.
The catalogue retains the original selection, including sources not rendered
for this purpose; no catalogue membership grants support for a candidate.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, model_validator

from ..schema_core import FrozenModel
from .life_source_origin import canonical, digest

MAX_VIEW_BYTES = 1_048_576


def _expected_view(snapshot, review_contract=None):
    from ..background_context_profile import slice_background_inner_life_snapshot
    from ..present_prompt import present_inner_life

    from .life_source_state_readings import STATE_REVIEW_CONTRACTS, life_source_profile

    from .life_source_review_request import LEAN_REVIEW_CONTRACTS

    profile = life_source_profile(review_contract)
    return present_inner_life(slice_background_inner_life_snapshot(
        snapshot.model_view(include_lifecycle_states=review_contract in STATE_REVIEW_CONTRACTS),
        profile,
        # The lean wire carries only the inventory columns anything reads.
        lean_source_inventory=review_contract in LEAN_REVIEW_CONTRACTS,
    ))


class LifeSourceView(FrozenModel):
    contract: Literal["life-source-view.1"] = "life-source-view.1"
    write_authority: Literal[False] = False
    semantic_coverage: Literal["not_assessed"] = "not_assessed"
    source_permission_coverage: Literal["not_assessed"] = "not_assessed"
    review_contract: Literal['life-source-review.1', 'life-source-review.2', 'life-source-review.3', 'life-source-review.4', 'life-source-review.5', 'life-source-review.6', 'life-source-review.7', 'life-source-review.8', 'life-source-review.9', 'life-source-review.10', 'life-source-review.11', 'life-source-review.12', 'life-source-review.13', 'life-source-review.14', 'life-source-review.15', 'life-source-review.16'] | None = Field(default=None, exclude_if=lambda value: value is None)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    capsule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_controls_json: str = Field(min_length=2, max_length=MAX_VIEW_BYTES)
    messages_json: str = Field(min_length=2, max_length=MAX_VIEW_BYTES)
    source_table_json: str = Field(min_length=2, max_length=MAX_VIEW_BYTES)
    unmatched_visible_source_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def bounded_canonical_carriers(self):
        for raw in (self.messages_json, self.source_table_json, self.provider_controls_json):
            if len(raw.encode("utf-8")) > MAX_VIEW_BYTES or canonical(json.loads(raw)) != raw:
                raise ValueError("Life source view carrier is noncanonical or exceeds its byte bound")
        return self

    def verify_snapshot(self, snapshot) -> "LifeSourceView":
        from ..background_context_profile import profile_audit_record
        from ..selected_source_composer import compile_selected_source_table

        checked = type(self).model_validate_json(self.model_dump_json())
        snapshot.identity_and_inventory_are_complete()
        origin = snapshot.life_source_origin
        if origin is None or (checked.snapshot_hash, checked.capsule_sha256) != (
            snapshot.snapshot_hash, origin.capsule_sha256,
        ):
            raise ValueError("Life source view differs from its original snapshot or Capsule")
        from companion_daemon.llm import provider_invocation_request_hash

        messages = json.loads(checked.messages_json)
        controls = json.loads(checked.provider_controls_json)
        if not isinstance(controls, dict) or set(controls) != {"temperature", "tools", "tool_choice", "identity_extras"}:
            raise ValueError("Life source view lacks complete provider request controls")
        if checked.provider_request_hash != "sha256:" + provider_invocation_request_hash(messages=messages, **controls):
            raise ValueError("Life source view differs from the actual provider request hash")
        payload = _payload(messages)
        from .life_source_state_readings import STATE_REVIEW_CONTRACTS, life_source_profile

        profile = life_source_profile(checked.review_contract)
        if (
            payload.get("inner_life_snapshot") != _expected_view(snapshot, checked.review_contract)
            or payload.get("background_context_profile") != profile_audit_record(profile)
        ):
            raise ValueError("Life source view differs from the actual purpose-filtered snapshot")
        table = compile_selected_source_table(capsule=origin.capsule(), include_subjective_history=True,
            include_lifecycle_states=checked.review_contract in STATE_REVIEW_CONTRACTS)
        if checked.source_table_json != table.payload_json:
            raise ValueError("Life source catalogue differs from its original Capsule selection")
        if checked.unmatched_visible_source_refs != _unmatched(payload, table.as_dict()):
            raise ValueError("Life source view omitted unmatched visible sources")
        return checked

    def verify_request(self, request) -> "LifeSourceView":
        checked = self.verify_snapshot(request.snapshot)
        expected_turn = {
            "inner_turn_id": request.inner_turn_id, "phase": request.phase,
            "subject_ref": request.subject_ref, "trigger_ref": request.trigger_ref,
            "purpose": request.purpose, "context_note": request.context_note,
            "subject_source_refs": list(request.subject_source_refs),
        }
        if (_payload(json.loads(checked.messages_json))["inner_turn"] != expected_turn
            or checked.request_binding_sha256 != digest(canonical(request.model_dump(mode="json")))):
            raise ValueError("Life source view belongs to another role request or correction")
        return checked


def _payload(messages):
    if (not isinstance(messages, list) or len(messages) != 2
        or [m.get("role") for m in messages] != ["system", "user"]):
        raise ValueError("Life source view requires the complete original role messages")
    value = json.loads(messages[1]["content"])
    if not isinstance(value, dict) or value.get("inner_turn", {}).get("purpose") != "world_stimulus_appraisal":
        raise ValueError("Life source view requires its original Life author purpose")
    return value


def _unmatched(payload, table):
    # Exact reference overlap is diagnostic only, not evidence eligibility.
    represented = {row['source_ref'] for row in table['source_references']}
    return tuple(sorted(set(payload['inner_life_snapshot'].get('source_refs', ())) - represented))


def prepare_life_source_view(*, request, messages, provider_controls: dict, provider_request_hash: str, review_contract=None) -> LifeSourceView:
    from ..selected_source_composer import compile_selected_source_table

    if request.purpose != "world_stimulus_appraisal" or request.snapshot.life_source_origin is None:
        raise ValueError("Life source preparation needs the original retained Capsule")
    origin = request.snapshot.life_source_origin
    from .life_source_state_readings import STATE_REVIEW_CONTRACTS

    table = compile_selected_source_table(capsule=origin.capsule(), include_subjective_history=True,
        include_lifecycle_states=review_contract in STATE_REVIEW_CONTRACTS)
    prepared = LifeSourceView(
        review_contract=review_contract,
        snapshot_hash=request.snapshot.snapshot_hash,
        capsule_sha256=origin.capsule_sha256,
        request_binding_sha256=digest(canonical(request.model_dump(mode="json"))),
        provider_request_hash=provider_request_hash,
        messages_json=canonical(messages), provider_controls_json=canonical(provider_controls),
        source_table_json=table.payload_json,
        unmatched_visible_source_refs=_unmatched(_payload(messages), table.as_dict()),
    )
    return prepared.verify_request(request)
