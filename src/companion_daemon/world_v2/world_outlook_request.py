"""Lossless outlook request transport; no evidence selection or source authority."""

from copy import deepcopy
import json

from .shared_string_view import (
    CONTRACT as SHARED_STRINGS,
    pack_shared_strings,
    unpack_shared_strings,
)


_COLUMNS = ("source_ref", "source_kind", "status", "allowed_statuses", "token")
_TABLE = "outlook-condition-sources-table.1"
_RECORDS_TABLE = "outlook-records-table.1"
_RECORD_TABLE_FIELDS = ("plan_schedules", "npc_capabilities", "npc_activity_location_capabilities")
RESPONSE_REFERENCE_CONTRACT = "outlook-source-references.1"
ROLLING_REFERENCE_CONTRACT = "outlook-source-references.3"
_PRIOR_ROLLING_CONTRACT = "outlook-source-references.2"
PRESENTATION_GUIDE = """Request encoding: a shared-string-view.1 wrapper is lossless. Read value,
resolving each exact prefix token using strings. If condition_sources is an
outlook-condition-sources-table.1, each row maps positionally to columns.
The same rule applies to plan_schedules, npc_capabilities and
npc_activity_location_capabilities in outlook-records-table.1.
These are the same complete values, not summaries. When response_reference_contract
is outlook-source-references.1, prefer copying exact dictionary aliases for references and hashes;
the host expands them against this request only, then validates all authority.
Otherwise output original expanded refs. Write narrative text yourself, never
dictionary aliases in prose. The draft schema and authority rules are unchanged.
In outlook-working-sources.2 a realized_story_history thread may be
{"retain_thread_id":"id"}; read the exact same full thread from current_outlook.threads.
This removes only a duplicate object, not any text or source."""
ROLLING_PRESENTATION_GUIDE = """In outlook-source-references.3, native-tool constants/enums use
expanded IDs. Prefer those expanded IDs in the response; the exact source dictionary remains readable.
To retain an unchanged prior opportunity use only {"retain_opportunity_id":"offered id"}; for an
unchanged prior thread use only {"retain_thread_id":"offered id"}. The host restores that complete
pinned object, including its exact prose and source bindings. No additional/override fields are legal.
New or revised prose must be your own text, never a dictionary alias. New and retained objects both
undergo the same complete authority, privacy, expiry and planning validation.
When StoryAnchor offers history_anchor_ref, select {"history_anchor_ref":"offered source_ref"}
instead of copying its hash/revision/event fields. The host restores that exact offered anchor;
you still decide which sources to link. Unknown references and override fields are rejected."""

_REFERENCE_FIELDS = frozenset({
    "world_id", "actor_ref", "parent_revision", "thread_id", "opportunity_id",
    "thread_refs", "after_opportunities", "source_ref", "authority_ref", "authority_hash", "basis_refs",
    "npc_ref", "participant_npc_refs", "location_ref", "target_plan_ref", "unchanged_token", "supersedes_occurrence_ref",
    "retain_opportunity_id", "retain_thread_id",
    "history_anchor_ref",
    "planning_key", "planning_keys",
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
    for field in _RECORD_TABLE_FIELDS:
        table = packet.get(field)
        if not isinstance(table, dict) or table.get("contract") != _RECORDS_TABLE:
            continue
        if (set(table) != {"contract", "columns", "rows"}
                or not isinstance(table["columns"], list) or not table["columns"]
                or not all(isinstance(column, str) for column in table["columns"])
                or len(table["columns"]) != len(set(table["columns"]))
                or not isinstance(table["rows"], list)):
            raise ValueError("invalid outlook records table")
        try:
            packet[field] = [dict(zip(table["columns"], row, strict=True)) for row in table["rows"]]
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid outlook records table rows") from exc
    if packet.get("condition_source_scope") == "outlook-working-sources.2":
        threads = {t["thread_id"]: t for t in packet.get("current_outlook", {}).get("threads", ())}
        for row in packet.get("realized_story_history", ()):
            if not isinstance(row, dict):
                continue
            thread = row.get("thread")
            if isinstance(thread, dict) and "retain_thread_id" in thread:
                identity = thread["retain_thread_id"]
                if set(thread) != {"retain_thread_id"} or not isinstance(identity, str) or identity not in threads:
                    raise ValueError("request thread selector must name an exact current thread without overrides")
                row["thread"] = deepcopy(threads[identity])
    return packet


def decode_outlook_response(raw, *, request_raw, response_tools=None):
    """Expand only explicit reference fields; never edit authored prose/choices.

    The marker is pinned in the stored request. Old requests keep their original
    interpretation, including failed attempts; this is not retrospective repair.
    """
    response = json.loads(raw)
    request = json.loads(request_raw)
    expanded = decode_outlook_request(request_raw)
    contract = expanded.get("response_reference_contract")
    if contract not in {RESPONSE_REFERENCE_CONTRACT, _PRIOR_ROLLING_CONTRACT, ROLLING_REFERENCE_CONTRACT}:
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

    response = visit(response)
    if contract in {_PRIOR_ROLLING_CONTRACT, ROLLING_REFERENCE_CONTRACT}:
        def reject_alias(value):
            if isinstance(value, str) and prefix and (prefix in value if contract == ROLLING_REFERENCE_CONTRACT else value.startswith(prefix)):
                raise ValueError("dictionary aliases are not new prose; use an exact prior-object selector or author full new text")
            if isinstance(value, dict):
                for child in value.values():
                    reject_alias(child)
            elif isinstance(value, list):
                for child in value:
                    reject_alias(child)
        reject_alias(response)
        if isinstance(response, dict) and isinstance(response.get("draft"), dict):
            view = expanded["current_outlook"]
            for field, selector, identity in (("opportunities", "retain_opportunity_id", "opportunity_id"),
                                               ("threads", "retain_thread_id", "thread_id")):
                offered = {n[identity]: {k: v for k, v in n.items() if k != "state"} for n in view[field]}
                restored = []
                for item in response["draft"].get(field, ()):
                    if isinstance(item, dict) and selector in item:
                        if set(item) != {selector} or not isinstance(item[selector], str) or item[selector] not in offered:
                            raise ValueError("prior outlook selector must name one complete pinned object without overrides")
                        restored.append(offered[item[selector]])
                    else:
                        restored.append(item)
                if field in response["draft"]:
                    response["draft"][field] = restored
            if expanded.get("response_tool_contract") in {"world-outlook-native.7", "world-outlook-native.8", "world-outlook-native.9"}:
                # Only the separately pinned provider schema grants selectors. A
                # marker in model text is never a replacement for that contract.
                if (not isinstance(response_tools, list) or len(response_tools) != 1
                        or response_tools[0].get("function", {}).get("name") != "world_outlook_v6"):
                    raise ValueError("missing exact pinned outlook response schema")
                anchor_schema = response_tools[0]["function"]["parameters"].get("$defs", {}).get("StoryAnchor", {})
            else:
                anchor_schema = expanded.get("draft_schema", {}).get("$defs", {}).get("StoryAnchor", {})
            anchors = {item["anchor"]["source_ref"]: item["anchor"] for item in expanded.get("history_offer", {}).get("items", ())}
            for thread in response["draft"].get("threads", ()):
                if not isinstance(thread, dict):
                    continue
                restored = []
                for anchor in thread.get("history_anchors", ()):
                    if isinstance(anchor, dict) and "history_anchor_ref" in anchor:
                        ref = anchor["history_anchor_ref"]
                        if ("history_anchor_ref" not in anchor_schema.get("properties", {})
                                or set(anchor) != {"history_anchor_ref"} or not isinstance(ref, str) or ref not in anchors):
                            if expanded.get("response_tool_contract") == "world-outlook-native.4":
                                raise ValueError(f"history anchor selector {ref!r} in thread {thread.get('thread_id')!r} must name one exact offered source without overrides; use only history_offer.items[].anchor.source_ref, not an unavailable anchor from the prior outlook")
                            raise ValueError("history anchor selector must name one exact offered source without overrides")
                        restored.append(anchors[ref])
                    else:
                        restored.append(anchor)
                if "history_anchors" in thread:
                    thread["history_anchors"] = restored
    return response


def encode_outlook_request(packet, *, byte_limit):
    plain = _wire(packet)
    if (packet.get("response_reference_contract") not in {RESPONSE_REFERENCE_CONTRACT, _PRIOR_ROLLING_CONTRACT, ROLLING_REFERENCE_CONTRACT}
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
    for field in _RECORD_TABLE_FIELDS:
        rows = packet.get(field)
        if not isinstance(rows, list) or len(rows) < 2 or not isinstance(rows[0], dict):
            continue
        columns = sorted(rows[0])
        if columns and all(isinstance(row, dict) and set(row) == set(columns) for row in rows):
            tabular[field] = {"contract": _RECORDS_TABLE, "columns": columns,
                              "rows": [[row[column] for column in columns] for row in rows]}
    if packet.get("condition_source_scope") == "outlook-working-sources.2" and isinstance(packet.get("realized_story_history"), list):
        threads = {t["thread_id"]: t for t in packet.get("current_outlook", {}).get("threads", ())}
        tabular["realized_story_history"] = [
            {**row, "thread": {"retain_thread_id": row["thread"]["thread_id"]}}
            if isinstance(row, dict) and isinstance(row.get("thread"), dict) and row["thread"] == threads.get(row["thread"].get("thread_id"))
            else row for row in packet.get("realized_story_history", ())]
    # The explicit history offer repeats exact 64-byte authority hashes already
    # shown in Context. Intern these as well; never trim sources to fit the cap.
    candidates = (plain, _wire(tabular), _wire(pack_shared_strings(tabular)),
                  _wire(pack_shared_strings(tabular, min_value_bytes=64)))
    encoded = min(candidates, key=lambda raw: len(raw.encode()))
    if decode_outlook_request(encoded) != packet:
        raise ValueError("outlook presentation changed the request")
    return encoded
