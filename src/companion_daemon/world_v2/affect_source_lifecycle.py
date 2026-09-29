"""Whether an authored Affect lost its accepted Appraisal authority over time."""


def source_appraisal_closed(*, proposal, projection) -> bool:
    """Recognize only complete, same-proposal, durably closed Appraisal refs.

    Missing or malformed sources are still errors, not expiry. This guard is
    shared by recovery selection and compilation so an impossible old effect
    is neither retried forever nor applied to a newer emotional state.
    """
    changes = [c for c in proposal.proposed_changes if c.kind == "affect_transition"]
    if len(changes) != 1:
        return False
    refs = changes[0].payload.value().get("appraisal_change_refs")
    authored = {c.change_id for c in proposal.proposed_changes if c.kind == "appraisal_transition"}
    if (not isinstance(refs, list) or not refs
            or any(not isinstance(ref, str) or ref not in authored for ref in refs)
            or len(set(refs)) != len(refs)):
        return False
    sources = [a for a in projection.appraisals if a.origin.change_id in refs]
    if len(sources) != len(refs) or {a.origin.change_id for a in sources} != set(refs):
        return False
    return any(a.status in {"expired", "superseded", "contradicted"}
               and a.closed_at is not None for a in sources)
