"""Parse current YAML text efficiently without caching file freshness."""

from copy import deepcopy
from functools import lru_cache

import yaml

_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@lru_cache(maxsize=8)
def _parse(text: str) -> object:
    return yaml.load(text, Loader=_SAFE_LOADER)


def load_yaml_text(text: str) -> object:
    """Return an independent tree; callers still read the current file each time.

    The cache key is the complete content, never the path or its timestamp.
    Validation and authority checks remain with the caller.
    """
    return deepcopy(_parse(text))
