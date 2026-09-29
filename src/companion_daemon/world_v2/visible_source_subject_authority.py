"""Typed source-use boundaries for the opt-in witness experiment.

Source ownership anchors pronouns and provenance. It is not the grammatical
subject of every proposition inside a report or settled environment. The model
still owns entailment; this module never classifies candidate prose.
"""
from __future__ import annotations

from .visible_source_closure_protocol import _eligible_reference

CONTRACT = "visible-source-subject-authority.1"
PREHISTORY_CONTRACT = "visible-source-subject-authority.2"
NONPERSONAL = frozenset({"general", "other", "none"})


def permits_source_subject(
    *, row: dict, pointer: str, claim_scope: str, subject_role: str,
    prehistory_authority: bool = False,
    allow_companion_activity_lifecycle: bool = True,
) -> bool:
    """Check an already exact-quoted field against its existing typed authority."""
    if not _eligible_reference(row):
        return False
    return _permits_eligible_source_subject(
        row=row, pointer=pointer, claim_scope=claim_scope, subject_role=subject_role,
        prehistory_authority=prehistory_authority,
        allow_companion_activity_lifecycle=allow_companion_activity_lifecycle,
    )


def source_subject_permissions(
    *, row: dict, pointers: list[str], prehistory_authority: bool = False,
    allow_companion_activity_lifecycle: bool = True,
) -> dict[str, list[list[str]]]:
    """Compile field permissions with one source validation for this batch.

    The result is local to this invocation; no model verdict or authority is
    cached. Callers still check exact scalar identity against the original pin.
    """
    if not _eligible_reference(row):
        return {}
    if prehistory_authority:
        from .visible_prehistory_readings import prehistory_field_permissions
        historical = prehistory_field_permissions(row)
        if historical is not None:
            return {pointer: historical.get(pointer, []) for pointer in pointers}
    return {pointer: [
        [scope, role]
        for scope in (
            "utterance_record", "accepted_intention", "activity_lifecycle",
            "environment", "external_fact", "subjective_history",
        )
        for role in ("companion", "counterpart", "general", "other", "none")
        if _permits_eligible_source_subject(
            row=row, pointer=pointer, claim_scope=scope, subject_role=role,
            prehistory_authority=prehistory_authority,
            allow_companion_activity_lifecycle=allow_companion_activity_lifecycle,
        )
    ] for pointer in pointers}


def _permits_eligible_source_subject(
    *, row: dict, pointer: str, claim_scope: str, subject_role: str,
    prehistory_authority: bool = False,
    allow_companion_activity_lifecycle: bool = True,
) -> bool:
    if prehistory_authority:
        from .visible_prehistory_readings import prehistory_field_permissions
        historical = prehistory_field_permissions(row)
        if historical is not None:
            return [claim_scope, subject_role] in historical.get(pointer, [])
    material = row["review_material"]
    owner = row.get("support_subject_role")
    if "subjective_history_support" in row:
        from .visible_subjective_source import subjective_direct_paths
        return (claim_scope == "subjective_history" and subject_role == owner == "companion"
                and pointer in subjective_direct_paths(row, [pointer]))
    if claim_scope == "subjective_history":
        return False
    if material.get("lane") == "recent_dialogue" and material.get("authority") == "companion_expression_record":
        return claim_scope == "utterance_record" and subject_role == owner
    if isinstance(row.get("settled_life_support"), dict):
        if pointer.startswith("/item/value/content/world_consequence/environment/"):
            return claim_scope in {"environment", "external_fact"} and subject_role in NONPERSONAL
        if pointer.startswith("/item/value/content/world_consequence/authorized_attempt_result/"):
            return claim_scope == "external_fact" and subject_role == owner
        return False
    if (
        material.get("kind") == "current_counterpart_report"
        or (material.get("lane") == "recent_dialogue" and material.get("authority") == "counterpart_report_only")
    ):
        if claim_scope == "utterance_record":
            return subject_role == owner
        # This permits uptake of the report, including its third-party subjects;
        # it does not make the report objective World truth or self-experience.
        return claim_scope == "external_fact" and subject_role in NONPERSONAL | {"counterpart"}
    activity = row.get("activity_support")
    if isinstance(activity, dict):
        if claim_scope == "accepted_intention":
            return (
                subject_role == owner
                and pointer.startswith("/item/value/accepted_intention/")
            )
        lifecycle_pointer = pointer in {
            "/item/value/status", "/item/value/started_at", "/item/value/ended_at",
        }
        lifecycle_subject_allowed = (
            subject_role == owner
            if allow_companion_activity_lifecycle
            else subject_role in NONPERSONAL
        )
        return (
            claim_scope == "activity_lifecycle"
            and lifecycle_pointer
            and lifecycle_subject_allowed
            and activity.get("status") in {"active", "completed", "in_progress"}
        )
    # Other source families retain their existing subject restriction. A new
    # reader must justify any wider authority; source ownership alone cannot.
    return subject_role == owner
