"""Short-lived material reads over one immutable projection and store snapshot.

Only synchronous work on the owning thread may reuse these results. No result,
missing-record decision or permission conclusion survives the context manager.
Unknown stores keep their ordinary read behaviour.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import get_ident

from .immutable_value_cache import _immutable, immutable_value_cache
from .schema_core import FrozenModel


_CURRENT = ContextVar("world_v2_context_read_scope", default=None)
_MAX_MATERIALS = 4096


class _ReadScope:
    def __init__(self, store, projection, snapshot):
        self.store = store
        self.projection = projection
        self.snapshot = snapshot
        self.owner = get_ident()
        self.closed = False
        self.materials = {}
        self.immutable_ids = set()

    def active_for(self, store):
        return not self.closed and self.owner == get_ident() and self.store is store


@contextmanager
def context_read_scope(store, projection):
    factory = getattr(store, "read_snapshot", None)
    if not callable(factory):
        # Also mask an outer scope when a nested caller uses an unknown store.
        token = _CURRENT.set(None)
        try:
            yield
        finally:
            _CURRENT.reset(token)
        return
    with factory() as snapshot:
        scope = _ReadScope(store, projection, snapshot)
        token = _CURRENT.set(scope)
        try:
            yield
        finally:
            scope.closed = True
            scope.materials.clear()
            scope.immutable_ids.clear()
            _CURRENT.reset(token)


def current_content_snapshot(store):
    scope = _CURRENT.get()
    return scope.snapshot if scope is not None and scope.active_for(store) else None


class _ImmutableTrace:
    """Record newly visited objects; failed checks never enter the known set."""

    def __init__(self, known):
        self.known = known
        self.added = set()

    def __contains__(self, key):
        return key in self.known or key in self.added

    def add(self, key):
        self.added.add(key)


@immutable_value_cache(maxsize=4096)
def _immutable_model(value):
    """Only object structure is memoized across scopes, never its authority."""
    return _immutable(value)


def _memoized(scope, kind, owners, parameters, compute):
    key = (kind, tuple(id(value) for value in owners), parameters)
    try:
        hash(key)
    except TypeError:
        return compute()
    existing = scope.materials.get(key)
    if existing is not None and all(a is b for a, b in zip(existing[0], owners)):
        return existing[1]
    result = compute()
    if len(scope.materials) < _MAX_MATERIALS:
        seen = _ImmutableTrace(scope.immutable_ids)
        # Source-proof pairs refer to persistent projection objects. Reuse
        # their pure deep-immutability check, without carrying a proof between
        # frames. Mutable nested fields never enter this weak identity cache.
        immutable = (
            all(_immutable_model(item) for item in result)
            if type(result) is tuple and all(isinstance(item, FrozenModel) for item in result)
            else _immutable(result, seen)
        )
        if immutable:
            # Retain the result and owners until exit, so visited IDs cannot
            # be recycled. Never retain IDs from a failed/mutable result.
            scope.materials[key] = (owners, result)
            scope.immutable_ids.update(seen.added)
    return result


def memoized_context_read(*, store, projection, kind, owners=(), parameters=(), compute):
    scope = _CURRENT.get()
    if scope is None or not scope.active_for(store) or scope.projection is not projection:
        return compute()
    return _memoized(scope, kind, owners, parameters, compute)


def has_projection_read_scope(matching_fields):
    scope = _CURRENT.get()
    return (
        scope is not None
        and not scope.closed
        and scope.owner == get_ident()
        and all(
            getattr(scope.projection, name, None) is value
            for name, value in matching_fields.items()
        )
    )


def memoized_projection_read(*, matching_fields, kind, owners=(), parameters=(), compute):
    """Share a proof only for this frame's exact immutable source collections."""
    scope = _CURRENT.get()
    if (
        scope is None
        or scope.closed
        or scope.owner != get_ident()
        or any(
            getattr(scope.projection, name, None) is not value
            for name, value in matching_fields.items()
        )
    ):
        return compute()
    return _memoized(scope, kind, owners, parameters, compute)
