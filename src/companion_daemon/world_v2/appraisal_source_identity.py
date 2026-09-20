"""Stable identities for the sources of accepted private appraisals."""

from __future__ import annotations

import hashlib
import json


def conversation_source_cluster_ref(*, actor_ref: str, channel: str) -> str:
    """Return the existing source-clustering.1 actor/channel scope identity.

    This is a conversation scope, not evidence that two events share a topic.
    Its bytes remain identical to the original Appraisal proposal compiler.
    """

    material = json.dumps(
        {"actor": actor_ref, "channel": channel},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "conversation:" + hashlib.sha256(material.encode()).hexdigest()
