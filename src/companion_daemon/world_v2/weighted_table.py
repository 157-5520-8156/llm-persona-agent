"""Replayable weighted draws. The seed is the function input; no RandomDraw event.

The table decides whether an opportunity exists. She decides the content and meaning.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pick_weighted_token(weights: Mapping[str, int], seed_material: object) -> str:
    items = [(token, int(mass)) for token, mass in sorted(weights.items()) if int(mass) > 0]
    if not items:
        raise ValueError("weighted table has no positive mass")
    total = sum(mass for _, mass in items)
    digest = hashlib.sha256(_canonical(seed_material).encode("utf-8")).digest()
    draw = int.from_bytes(digest[:8], "big") % total
    cursor = 0
    for token, mass in items:
        cursor += mass
        if draw < cursor:
            return token
    raise AssertionError("weighted table draw fell off the end")


def inject_nothing_mass(
    weights: Mapping[str, int],
    *,
    nothing_ref: str,
    space_bp: int = 10_000,
) -> dict[str, int]:
    result = {token: int(mass) for token, mass in weights.items() if token != nothing_ref}
    result[nothing_ref] = max(space_bp - sum(result.values()), 0)
    return result


__all__ = ["inject_nothing_mass", "pick_weighted_token"]
