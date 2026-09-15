"""Lossless JSON presentation: intern repeated values, never select evidence.

The original tree remains the authority. This module knows nothing about roles,
sources, model decisions, storage or providers. It only makes repeated strings
cheap to present, and can reconstruct the exact JSON value for verification.
"""
from collections import Counter
from copy import deepcopy
import json

CONTRACT = "shared-string-view.1"


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pack_shared_strings(value):
    counts = Counter()
    literals = set()

    def collect(node):
        if isinstance(node, str):
            counts[node] += 1
            literals.add(node)
        elif isinstance(node, dict):
            literals.update(node)
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(value)
    prefix = "@s:"
    while any(prefix in literal for literal in literals):
        prefix = "@" + prefix
    # Size is a transport property, not semantic selection. Every value stays.
    selected = sorted(s for s, n in counts.items() if n >= 2 and len(s.encode()) >= 72)
    names = {s: f"{prefix}{i}" for i, s in enumerate(selected)}

    def replace(node):
        if isinstance(node, str):
            return names.get(node, node)
        if isinstance(node, dict):
            return {key: replace(child) for key, child in node.items()}
        if isinstance(node, list):
            return [replace(child) for child in node]
        return deepcopy(node)

    packet = {"contract": CONTRACT, "prefix": prefix,
              "strings": {name: text for text, name in names.items()}, "value": replace(value)}
    if len(_wire(packet).encode()) >= len(_wire(value).encode()):
        packet = {"contract": CONTRACT, "prefix": prefix, "strings": {}, "value": deepcopy(value)}
    if unpack_shared_strings(packet) != value:
        raise ValueError("shared string presentation changed its original value")
    return packet


def unpack_shared_strings(packet):
    if not isinstance(packet, dict) or set(packet) != {"contract", "prefix", "strings", "value"} or packet["contract"] != CONTRACT:
        raise ValueError("invalid shared string presentation")
    prefix, strings = packet["prefix"], packet["strings"]
    if not isinstance(prefix, str) or not prefix or not isinstance(strings, dict):
        raise ValueError("invalid shared string dictionary")
    if set(strings) != {f"{prefix}{i}" for i in range(len(strings))}:
        raise ValueError("shared string keys must be contiguous")
    if any(not isinstance(s, str) or prefix in s for s in strings.values()):
        raise ValueError("shared strings cannot contain reference tokens")
    uses = Counter()

    def expand(node):
        if isinstance(node, str) and prefix in node:
            if node not in strings:
                raise ValueError("unresolved shared string token")
            uses[node] += 1
            return strings[node]
        if isinstance(node, dict):
            if any(prefix in key for key in node):
                raise ValueError("shared string keys cannot be encoded")
            return {key: expand(child) for key, child in node.items()}
        if isinstance(node, list):
            return [expand(child) for child in node]
        return deepcopy(node)

    value = expand(packet["value"])
    if set(uses) != set(strings):
        raise ValueError("unused shared string entry")
    return value
