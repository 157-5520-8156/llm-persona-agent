"""Bounded memoization for pure computations over immutable projected models.

Unlike a value-key LRU, a hit does not recursively hash the complete model.
Only immutable inputs/results are retained, inputs by weak reference. This is
not an authorization cache: callers must still resolve current source presence,
actor, privacy, status and time before using a computed value.
"""

from collections import OrderedDict, namedtuple
from datetime import date, datetime, time, timedelta
from functools import wraps
from threading import RLock
from weakref import ref

from .schema_core import FrozenModel

_CacheInfo = namedtuple("CacheInfo", "hits misses maxsize currsize")


def _immutable(value, seen=None):
    if isinstance(
        value, (str, bytes, int, float, bool, type(None), date, datetime, time, timedelta)
    ):
        return True
    if seen is None:
        seen = set()
    key = id(value)
    if key in seen:
        return True
    seen.add(key)
    if isinstance(value, (tuple, frozenset)):
        return all(_immutable(v, seen) for v in value)
    if isinstance(value, FrozenModel) and type(value).model_config.get("frozen") is True:
        return all(_immutable(getattr(value, field), seen) for field in type(value).model_fields)
    return False


def immutable_value_cache(*, maxsize):
    """Cache pure functions of frozen models and scalar options; new objects recompute.

    FrozenModel's supported mutation path is model_copy(), which has a new
    identity. Mutable nested fields disable caching. Weak-reference identity
    checks prevent object-id reuse and do not retain entire old projections.
    """
    if type(maxsize) is not int or maxsize <= 0:
        raise ValueError("immutable cache needs a positive finite bound")

    def decorate(function):
        entries = OrderedDict()
        lock = RLock()
        hits = misses = 0

        @wraps(function)
        def cached(*args, **kwargs):
            nonlocal hits, misses
            values = (*args, *(v for _, v in sorted(kwargs.items())))
            models = []
            components = []
            for value in values:
                if (
                    isinstance(value, FrozenModel)
                    and type(value).model_config.get("frozen") is True
                ):
                    models.append(value)
                    components.append(("model", id(value)))
                elif type(value) in (str, bytes, int, bool, type(None)):
                    components.append(("scalar", type(value), value))
                else:
                    return function(*args, **kwargs)
            key = (len(args), tuple(sorted(kwargs)), *components)
            with lock:
                entry = entries.get(key)
                if entry is not None and all(
                    pointer() is value for pointer, value in zip(entry[0], models)
                ):
                    entries.move_to_end(key)
                    hits += 1
                    return entry[1]
                misses += 1
            # Duplicate pure work on a concurrent miss is harmless; no caller
            # holds the cache mutex during serialization or validation.
            result = function(*args, **kwargs)
            if all(_immutable(value) for value in models) and _immutable(result):
                with lock:
                    entries[key] = (tuple(ref(value) for value in models), result)
                    entries.move_to_end(key)
                    while len(entries) > maxsize:
                        entries.popitem(last=False)
            return result

        def cache_info():
            with lock:
                return _CacheInfo(hits, misses, maxsize, len(entries))

        def cache_clear():
            nonlocal hits, misses
            with lock:
                entries.clear()
                hits = misses = 0

        cached.cache_info = cache_info
        cached.cache_clear = cache_clear
        return cached

    return decorate
