"""Ledger-backed adult-media eligibility.

Adult intensity is a hard boundary, not a character decision.  The deployment
switch defaults off, and the authorizer still fail-closes unless the ledger
holds the dedicated CapabilityGranted / ConsentGranted pair.  Relationship
stage remains a separate floor; this module never invents one.
"""

from __future__ import annotations

from datetime import datetime


ADULT_MEDIA_CAPABILITY_ID = "capability:world-v2:adult-media"
ADULT_MEDIA_CONSENT_ID = "consent:world-v2:adult-media"
ADULT_MEDIA_CAPABILITY_KIND = "media_render"
ADULT_MEDIA_ACTION_SCOPE = "media_render"


def _window_covers(values: object, at_logical_time: datetime) -> bool:
    valid_from = getattr(values, "valid_from", None)
    expires_at = getattr(values, "expires_at", None)
    if valid_from is not None and valid_from > at_logical_time:
        return False
    return expires_at is None or at_logical_time < expires_at


def adult_media_is_authorized(
    *, enabled: bool, projection: object, at_logical_time: datetime | None
) -> bool:
    """True only when the switch is on and both ledger grants are active."""

    if not enabled or at_logical_time is None:
        return False
    capability = next(
        (
            item
            for item in getattr(projection, "capability_grants", ())
            if getattr(item, "grant_id", None) == ADULT_MEDIA_CAPABILITY_ID
        ),
        None,
    )
    consent = next(
        (
            item
            for item in getattr(projection, "consent_grants", ())
            if getattr(item, "consent_id", None) == ADULT_MEDIA_CONSENT_ID
        ),
        None,
    )
    if capability is None or consent is None:
        return False
    cap_values = getattr(capability, "values", None)
    consent_values = getattr(consent, "values", None)
    if cap_values is None or consent_values is None:
        return False
    if getattr(cap_values, "state", None) != "active":
        return False
    if getattr(consent_values, "status", None) != "active":
        return False
    if getattr(cap_values, "capability_kind", None) != ADULT_MEDIA_CAPABILITY_KIND:
        return False
    if ADULT_MEDIA_ACTION_SCOPE not in tuple(getattr(consent_values, "action_scope_refs", ())):
        return False
    return _window_covers(cap_values, at_logical_time) and _window_covers(
        consent_values, at_logical_time
    )


__all__ = [
    "ADULT_MEDIA_ACTION_SCOPE",
    "ADULT_MEDIA_CAPABILITY_ID",
    "ADULT_MEDIA_CAPABILITY_KIND",
    "ADULT_MEDIA_CONSENT_ID",
    "adult_media_is_authorized",
]
