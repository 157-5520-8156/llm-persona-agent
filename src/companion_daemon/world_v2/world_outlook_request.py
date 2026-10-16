"""Lossless outlook request transport; no evidence selection or source authority."""

import json

from .shared_string_view import (
    CONTRACT as SHARED_STRINGS,
    pack_shared_strings,
    unpack_shared_strings,
)


_COLUMNS = ("source_ref", "source_kind", "status", "allowed_statuses", "token")
_TABLE = "outlook-condition-sources-table.1"
RESPONSE_REFERENCE_CONTRACT = "outlook-source-references.1"
PRESENTATION_GUIDE = """Request encoding: a shared-string-view.1 wrapper is lossless. Read value,
resolving each exact prefix token using strings. If condition_sources is an
outlook-condition-sources-table.1, each row maps positionally to columns.
These are the same complete sources, not summaries. When response_reference_contract
is outlook-source-references.1, prefer copying exact dictionary aliases for references and hashes;
the host expands them against this request only, then validates all authority.
Otherwise output original expanded refs. Write narrative text yourself, never
dictionary aliases in prose. The draft schema and authority rules are unchanged."""

_REFERENCE_FIELDS = frozenset({
    "world_id", "actor_ref", "parent_revision", "thread_id", "opportunity_id",
    "thread_refs", "after_opportunities", "source_ref", "authority_ref", "authority_hash", "basis_refs",
    "npc_ref", "participant_npc_refs", "location_ref", "target_plan_ref", "unchanged_token", "supersedes_occurrence_ref",
})


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


def decode_outlook_response(raw, *, request_raw):
    """Expand only explicit reference fields; never edit authored prose/choices.

    The marker is pinned in the stored request. Old requests keep their original
    interpretation, including failed attempts; this is not retrospective repair.
    """
    response = json.loads(raw)
    request = json.loads(request_raw)
    expanded = decode_outlook_request(request_raw)
    if expanded.get("response_reference_contract") != RESPONSE_REFERENCE_CONTRACT:
        return response
    strings = request.get("strings", {}) if request.get("contract") == SHARED_STRINGS else {}
    prefix = request.get("prefix") if strings else None

    def reference(value):
        if isinstance(value, str) and prefix and value.startswith(prefix):
            if value not in strings:
                raise ValueError("outlook response uses an unknown reference alias")
            return strings[value]
        if isinstance(value, list):
            return [reference(item) for item in value]
        return value

    def visit(value):
        if isinstance(value, dict):
            return {key: reference(child) if key in _REFERENCE_FIELDS else visit(child)
                    for key, child in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    return visit(response)


def encode_outlook_request(packet, *, byte_limit):
    plain = _wire(packet)
    if (packet.get("response_reference_contract") != RESPONSE_REFERENCE_CONTRACT
            and len(plain.encode()) <= byte_limit):
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
    # The explicit history offer repeats exact 64-byte authority hashes already
    # shown in Context. Intern these as well; never trim sources to fit the cap.
    candidates = (plain, _wire(tabular), _wire(pack_shared_strings(tabular)),
                  _wire(pack_shared_strings(tabular, min_value_bytes=64)))
    encoded = min(candidates, key=lambda raw: len(raw.encode()))
    if decode_outlook_request(encoded) != packet:
        raise ValueError("outlook presentation changed the request")
    return encoded
