"""Lossless outlook request transport; no evidence selection or source authority."""

import json

from .shared_string_view import (
    CONTRACT as SHARED_STRINGS,
    pack_shared_strings,
    unpack_shared_strings,
)


_COLUMNS = ("source_ref", "source_kind", "status", "allowed_statuses", "token")
_TABLE = "outlook-condition-sources-table.1"
PRESENTATION_GUIDE = """Request encoding: a shared-string-view.1 wrapper is lossless. Read value,
resolving each exact prefix token using strings. If condition_sources is an
outlook-condition-sources-table.1, each row maps positionally to columns.
These are the same complete sources, not summaries. Output original expanded
source refs and tokens, never string-dictionary aliases. The draft schema and
all authority rules are unchanged."""


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_outlook_request(raw):
    packet = json.loads(raw)
    if packet.get("contract") == SHARED_STRINGS:
        packet = unpack_shared_strings(packet)
    sources = packet.get("condition_sources")
    if isinstance(sources, dict) and sources.get("contract") == _TABLE:
        if set(sources) != {"contract", "columns", "rows"} or sources["columns"] != list(_COLUMNS):
            raise ValueError("invalid outlook source table")
        packet["condition_sources"] = [
            dict(zip(_COLUMNS, row, strict=True)) for row in sources["rows"]
        ]
    return packet


def encode_outlook_request(packet, *, byte_limit):
    plain = _wire(packet)
    if len(plain.encode()) <= byte_limit:
        return plain
    # The original JSON carrier turns tuples into arrays as well.
    packet = json.loads(plain)
    tabular = dict(packet)
    sources = packet["condition_sources"]
    if sources and all(set(s) == set(_COLUMNS) for s in sources):
        tabular["condition_sources"] = {
            "contract": _TABLE,
            "columns": list(_COLUMNS),
            "rows": [[source[key] for key in _COLUMNS] for source in sources],
        }
    candidates = (plain, _wire(tabular), _wire(pack_shared_strings(tabular)))
    encoded = min(candidates, key=lambda raw: len(raw.encode()))
    if decode_outlook_request(encoded) != packet:
        raise ValueError("outlook presentation changed the request")
    return encoded
