"""Bounded exact-text memoization; never caches relevance or eligibility."""

from collections import OrderedDict, namedtuple
from functools import wraps
from sys import getsizeof
from threading import RLock

_Info = namedtuple("FeatureCacheInfo", "hits misses maxsize currsize max_bytes retained_bytes")


def lexical_feature_cache(*, max_bytes=8 * 1024 * 1024, maxsize=4096):
    """Cache immutable string features with both byte and entry limits.

    Account for text, feature strings and containers conservatively; shared
    strings can be counted more than once. Interpreter/lock overhead remains
    additional and bounded by the entry count. Oversized values are computed
    normally without being retained. Each decorated tokenizer owns its cache.
    """
    if any(type(value) is not int or value <= 0 for value in (max_bytes, maxsize)):
        raise ValueError("feature cache bounds must be positive integers")

    def decorate(function):
        entries = OrderedDict()
        lock = RLock()
        retained = hits = misses = 0

        @wraps(function)
        def cached(text):
            nonlocal retained, hits, misses
            if type(text) is not str or len(text) > 4096:
                return function(text)
            with lock:
                entry = entries.get(text)
                if entry is not None:
                    entries.move_to_end(text)
                    hits += 1
                    return entry[0]
                misses += 1
            result = function(text)
            if type(result) is not frozenset or any(type(term) is not str for term in result):
                return result
            weight = (
                getsizeof(text)
                + getsizeof(result)
                + sum(map(getsizeof, result))
                + getsizeof((result, 0))
                + 128
            )
            if weight <= max_bytes:
                with lock:
                    previous = entries.pop(text, None)
                    if previous is not None:
                        retained -= previous[1]
                    entries[text] = (result, weight)
                    retained += weight
                    while len(entries) > maxsize or retained > max_bytes:
                        _, (_, old_weight) = entries.popitem(last=False)
                        retained -= old_weight
            return result

        def cache_info():
            with lock:
                return _Info(hits, misses, maxsize, len(entries), max_bytes, retained)

        def cache_clear():
            nonlocal retained, hits, misses
            with lock:
                entries.clear()
                retained = hits = misses = 0

        cached.cache_info = cache_info
        cached.cache_clear = cache_clear
        return cached

    return decorate
