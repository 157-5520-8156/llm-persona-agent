"""Bind shared source compilation to the exact original visible input.

Chat-only trigger/participant proofs stay here. Shared source material cannot
by itself authorize a visible receipt or a Life write.
"""
from __future__ import annotations

from .context_capsule import ContextCapsule
from .deliberation import ModelInput
from .selected_source_composer import (
    PLANNED_SOURCE_TABLE_CONTRACT as PLANNED_SOURCE_TABLE_CONTRACT,
    PREHISTORY_SOURCE_TABLE_CONTRACT as PREHISTORY_SOURCE_TABLE_CONTRACT,
    SETTLED_LIFE_SOURCE_TABLE_CONTRACT as SETTLED_LIFE_SOURCE_TABLE_CONTRACT,
    SUBJECTIVE_SOURCE_TABLE_CONTRACT as SUBJECTIVE_SOURCE_TABLE_CONTRACT,
    VISIBLE_SOURCE_TABLE_CONTRACT as VISIBLE_SOURCE_TABLE_CONTRACT,
    SelectedSourceTable,
    _compose_source_materials,
    _json,
)
from .visible_review_context import compile_visible_selected_source_context
from .source_time_comparison import verify_time_comparison, with_time_comparison


class VisibleSourceTable(SelectedSourceTable):
    """Historical visible table type; shared tables are not visible receipts."""

    __slots__ = ()

    def source_references(self) -> tuple[dict[str, object], ...]:
        verify_time_comparison(self.as_dict())
        return super().source_references()


def _current_report(request: ModelInput, proof: dict) -> dict | None:
    trigger = request.trigger_message
    if trigger is None or proof["subjects"].get("counterpart_actor_ref") != trigger.actor:
        return None
    # The public proof producer verifies the exact original Observation,
    # text, actor, revision and hash before granting this participant binding.
    dialogue = next(
        entry
        for entry in proof["entries"]
        if (
            entry["lane"] == "recent_dialogue"
            and entry["item"]["value"]["dialogue_id"]
            == f"dialogue:observation:{trigger.observation_ref}"
        )
    )
    return {
        "kind": "current_counterpart_report",
        "packet_contract": "current-counterpart-report-packet.1",
        "authority": "report_only_not_external_truth",
        "privacy_class": dialogue["privacy_class"],
        "availability": "available",
        "epistemic_status": "counterpart_report_only_not_objective_truth_or_companion_experience",
        "permits_natural_visible_uptake_without_world_claim": True,
        "natural_uptake_does_not_need_attribution_phrase": True,
        "source_refs": [trigger.event_ref],
        "message": trigger.model_dump(mode="json"),
        "does_not_authorize": [
            "added_or_changed_subject_time_occurrence_or_status",
            "added_detail_or_motive",
            "objective_world_fact",
            "companion_experience",
            "durable_world_mutation",
        ],
    }


def compile_visible_source_table(
    *, request: ModelInput, capsule: ContextCapsule, include_subjective_history: bool = False,
    include_time_comparison: bool = True,
) -> VisibleSourceTable:
    """Preserve exact request binding; new tables include derived UTC display."""
    if type(include_subjective_history) is not bool:
        raise TypeError("subjective history selection flag must be boolean")
    if type(include_time_comparison) is not bool:
        raise TypeError("time comparison selection flag must be boolean")
    proof = compile_visible_selected_source_context(request=request, capsule=capsule)
    report = _current_report(request, proof)
    payload = _compose_source_materials(
        capsule=capsule, proof=proof, include_subjective_history=include_subjective_history,
        supplemental_entries=(report,) if report is not None else (),
    )
    payload["pin"] = proof["visible_review_projection"]
    if include_time_comparison:
        payload = with_time_comparison(payload)
    return VisibleSourceTable(payload_json=_json(payload))


__all__ = ["VISIBLE_SOURCE_TABLE_CONTRACT", "VisibleSourceTable", "compile_visible_source_table"]
