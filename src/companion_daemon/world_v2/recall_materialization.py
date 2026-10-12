"""Bounded pure material reuse, after the caller's current source checks.

Complete typed inputs identify a material, never an old authorization verdict.
Unsupported or mutable values always use the ordinary constructing path.
"""

from collections import OrderedDict
from datetime import datetime, timezone
from threading import RLock
import math
from zoneinfo import ZoneInfo

from weakref import ref
from pydantic import BaseModel
from pydantic_core import TzInfo


def _immutable_material(value):
    if type(value) in (str, bytes, int, bool, type(None)):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, datetime):
        return type(value) is datetime and type(value.tzinfo) in (timezone, ZoneInfo, TzInfo)
    if type(value) in (tuple, frozenset):
        return all(_immutable_material(item) for item in value)
    if isinstance(value, BaseModel) and type(value).model_config.get("frozen") is True:
        return all(_immutable_material(getattr(value, name)) for name in type(value).model_fields)
    return False


# Cache one complete immutability result per model, without rebuilding the
# generic argument/weak-reference wrapper on every field of every document.
# JSON is filled only when a dependency key actually needs it.
_MODEL_CACHE = OrderedDict()
_MODEL_LOCK = RLock()
_MODEL_BYTES = 0
_MODEL_LIMIT = 16384
_MODEL_BYTE_LIMIT = 64 * 1024 * 1024


def _trim_models():
    global _MODEL_BYTES
    while len(_MODEL_CACHE) > _MODEL_LIMIT or _MODEL_BYTES > _MODEL_BYTE_LIMIT:
        _, entry = _MODEL_CACHE.popitem(last=False)
        _MODEL_BYTES -= entry[3]


def _model_info(value):
    global _MODEL_BYTES
    if type(value).model_config.get("frozen") is not True:
        return None
    key = id(value)
    with _MODEL_LOCK:
        previous = _MODEL_CACHE.get(key)
        if previous is not None and previous[0]() is value:
            _MODEL_CACHE.move_to_end(key)
            return previous
    immutable = _immutable_material(value)
    entry = [ref(value), immutable, None, 0]
    with _MODEL_LOCK:
        previous = _MODEL_CACHE.pop(key, None)
        if previous is not None:
            _MODEL_BYTES -= previous[3]
        _MODEL_CACHE[key] = entry
        _trim_models()
    return entry


def immutable_material(value):
    if isinstance(value, BaseModel):
        info = _model_info(value)
        return info is not None and info[1]
    return _immutable_material(value)


def _model_json(value):
    global _MODEL_BYTES
    info = _model_info(value)
    if info is None or not info[1]:
        return None
    with _MODEL_LOCK:
        existing = info[2]
    if existing is not None:
        return existing
    rendered = value.model_dump_json()
    with _MODEL_LOCK:
        if _MODEL_CACHE.get(id(value)) is info and info[2] is None:
            info[2] = rendered
            info[3] = 4 * len(rendered.encode())
            _MODEL_BYTES += info[3]
            _trim_models()
    return rendered


def material_key(value):
    if type(value) in (str, bytes):
        return value
    if type(value) in (int, bool, type(None)):
        return (type(value), value)
    if isinstance(value, datetime):
        return (datetime, value.isoformat(), value.fold) if _immutable_material(value) else None
    if isinstance(value, BaseModel):
        rendered = _model_json(value)
        return (type(value), rendered) if rendered is not None else None
    if type(value) is tuple:
        parts = tuple(material_key(item) for item in value)
        return parts if all(item is not None for item in parts) else None
    return None


def _key_size(key):
    # Conservative allowance for dependency containers and the model they
    # retain. The bound is an estimate, not an assertion about process RSS.
    if isinstance(key, (str, bytes)):
        return 128 + 4 * (len(key.encode()) if isinstance(key, str) else len(key))
    if type(key) is tuple:
        return 64 + sum(_key_size(item) for item in key)
    return 64


class RecallMaterialPool:
    """Keep only a bounded latest material per logical source/window identity."""

    def __init__(self, *, capacity=4096):
        self._capacity = capacity
        self._entries = OrderedDict()
        self._bytes = 0
        self._byte_limit = 64 * 1024 * 1024
        self._lock = RLock()

    def materialize(self, identity, inputs, build):
        key = material_key(inputs)
        if key is None:
            return build()
        with self._lock:
            previous = self._entries.get(identity)
            if previous is not None and previous[0] == key:
                self._entries.move_to_end(identity)
                return previous[1]
        result = build()
        if immutable_material(result):
            with self._lock:
                size = _key_size(key)
                previous = self._entries.pop(identity, None)
                if previous is not None:
                    self._bytes -= previous[2]
                if size <= self._byte_limit:
                    self._entries[identity] = (key, result, size)
                    self._bytes += size
                while len(self._entries) > self._capacity or self._bytes > self._byte_limit:
                    _, removed = self._entries.popitem(last=False)
                    self._bytes -= removed[2]
        return result
