"""Bounded content-address lookup for the installed Observation Fact producer.

That producer accepts one exact 1..256-codepoint substring and retains its
UTF-8 SHA-256. Enumerating that finite source space recovers the bound bytes;
it does not rank text, classify a predicate, or decide a candidate's meaning.
"""
from functools import lru_cache
import hashlib

from .fact_observation_value import FactObservationValueBinding

MAX_SOURCE_CODEPOINTS = 4096
MAX_VALUE_CODEPOINTS = 256


@lru_cache(maxsize=128)
def _lookup_exact_value(source_excerpt: str, value_hash: str) -> str | None:
    """Cache content identity only, including absence; never a semantic verdict."""
    characters = tuple(character.encode("utf-8") for character in source_excerpt)
    target = bytes.fromhex(value_hash)
    for start in range(len(characters)):
        running = hashlib.sha256()
        for end in range(start, min(start + MAX_VALUE_CODEPOINTS, len(characters))):
            running.update(characters[end])
            if running.digest() == target:
                return source_excerpt[start:end + 1]
    return None


def resolve_observation_fact_value(*, binding: FactObservationValueBinding, source_excerpt: str) -> str:
    """Recover exact bound bytes or fail closed, without normalizing the text.

    The caller must already have the source's original authority and privacy
    checks. A match verifies the retained value identity only, not its entailment
    for a visible claim. Repeated equal spans identify the same retained value.
    """
    if not isinstance(binding, FactObservationValueBinding):
        raise ValueError("Observation Fact lookup requires its typed value binding")
    checked = FactObservationValueBinding.model_validate_json(binding.model_dump_json(), strict=True)
    if not isinstance(source_excerpt, str) or not 1 <= len(source_excerpt) <= MAX_SOURCE_CODEPOINTS:
        raise ValueError("Observation Fact lookup source exceeds its 1..4096 codepoint bound")
    value = _lookup_exact_value(source_excerpt, checked.value_hash)
    if value is None:
        raise ValueError("exact accepted Fact value is unavailable in the bounded Observation")
    # A cache hit never bypasses exact quote/hash validation against this source.
    return checked.select(source_excerpt=source_excerpt, quoted_value=value)
