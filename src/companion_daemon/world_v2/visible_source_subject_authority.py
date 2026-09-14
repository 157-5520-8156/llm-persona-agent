"""Typed source-use boundaries for the opt-in witness experiment.

Source ownership anchors pronouns and provenance. It is not the grammatical
subject of every proposition inside a report or settled environment. The model
still owns entailment; this module never classifies candidate prose.
"""
from __future__ import annotations

from .visible_source_closure_protocol import _eligible_reference

CONTRACT = "visible-source-subject-authority.1"
NONPERSONAL = frozenset({"general", "other", "none"})


def permits_source_subject(*, row: dict, pointer: str, claim_scope: str, subject_role: str) -> bool:
    """Check an already exact-quoted field against its existing typed authority."""
    if not _eligible_reference(row):
        return False
    material = row["review_material"]
    owner = row.get("support_subject_role")
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
        if subject_role != owner:
            return False
        if claim_scope == "accepted_intention":
            return pointer.startswith("/item/value/accepted_intention/")
        return claim_scope == "activity_lifecycle" and activity.get("status") in {"active", "completed", "in_progress"}
    # Other source families retain their existing subject restriction. A new
    # reader must justify any wider authority; source ownership alone cannot.
    return subject_role == owner
