"""Address immutable projected rows without caching any authority decision."""

from collections import OrderedDict
from threading import RLock
from operator import attrgetter

from .schema_core import FrozenModel


_INDEXES = OrderedDict()
_LOCK = RLock()
_MAX_INDEXES = 32
_MAX_INDEX_ROWS = 100_000
_MAX_RETAINED_ROWS = 200_000
_RETAINED_ROWS = 0
_BUILD_AFTER_LOOKUPS = 8


def _build_index(rows, attribute):
    index = {}
    path = attribute.split(".")
    for row in rows:
        key = row
        for part in path:
            if (not isinstance(key, FrozenModel)
                or type(key).model_config.get("frozen") is not True
                or part not in type(key).model_fields):
                return False
            key = getattr(key, part)
        if not isinstance(key, (str, int, type(None))):
            return False
        index.setdefault(key, row)
    return index


def lookup_first(rows, attribute: str, value, *, predicate=None):
    """Equivalent to the first matching row in this exact current collection.

    Strongly retaining the tuple prevents object-id reuse. Different snapshots
    or collections never share an index, even if their cursors are equal.
    Mutable collections/rows and non-scalar keys use the ordinary scan. The
    caller must still verify the returned row's current provenance and access.
    """
    global _RETAINED_ROWS
    get_key = attrgetter(attribute)
    def scan():
        return next((row for row in rows if get_key(row) == value
                     and (predicate is None or predicate(row))), None)

    if type(rows) is not tuple or not 8 <= len(rows) <= _MAX_INDEX_ROWS:
        return scan()
    cache_key = (id(rows), attribute)
    with _LOCK:
        cached = _INDEXES.get(cache_key)
        if cached is not None and cached[0] is rows:
            _INDEXES.move_to_end(cache_key)
            index = cached[1]
            uses = cached[2] + 1
        else:
            index, uses = None, 1
        # Single-use commit projections should not pay for a whole-history
        # index. Build only when this same collection is repeatedly addressed.
        if index is None and uses >= _BUILD_AFTER_LOOKUPS:
            index = _build_index(rows, attribute)
        if cached is None or cached[0] is not rows:
            _RETAINED_ROWS += len(rows) - (len(cached[0]) if cached is not None else 0)
        _INDEXES[cache_key] = (rows, index, uses)
        while len(_INDEXES) > _MAX_INDEXES or _RETAINED_ROWS > _MAX_RETAINED_ROWS:
            _, evicted = _INDEXES.popitem(last=False)
            _RETAINED_ROWS -= len(evicted[0])
    # Never hold the cache mutex while executing a caller's predicate or a
    # mutable-row fallback scan. Index dictionaries are immutable after build.
    if index is None or index is False:
        return scan()
    try:
        row = index.get(value)
    except TypeError:
        return scan()
    if row is None or predicate is None or predicate(row):
        return row
    # A further current-state predicate is never cached. Preserve the old
    # first-match semantics even for a collection with duplicate addresses.
    return scan()
