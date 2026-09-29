"""Lossless presentation of repeated opaque identifiers, with exact decoding.

No facts, permissions or choices are selected here. Full identifiers appear
once in the request dictionary; outputs may use them or their exact aliases.
The dictionary belongs to the pinned request, never to a model response.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import re

CONTRACT = "opaque-reference-wire.1"
HOST_CONTRACT = "opaque-reference-wire.2"
ORDINAL_CONTRACT = "opaque-reference-wire.3"
BINDINGS_CONTRACT = "opaque-reference-bindings.2"
FIELD = "reference_dictionary"
_HASH = re.compile(r"[0-9a-f]{32}")


def _wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _opaque(value):
    return (isinstance(value, str) and 64 <= len(value) <= 4096
            and value.isascii() and not any(c.isspace() for c in value)
            and _HASH.search(value) is not None)


def pack_reference_view(value: dict) -> dict:
    if FIELD in value:
        raise ValueError("reference dictionary field already belongs to the input")
    counts = Counter()
    literals = []

    def collect(node):
        if isinstance(node, str):
            literals.append(node)
            if _opaque(node):
                counts[node] += 1
        elif isinstance(node, dict):
            literals.extend(node)
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(value)
    prefix = "@r:"
    while any(prefix in literal for literal in literals):
        prefix = "@" + prefix
    forward = {}
    entries = {}
    for original, count in counts.items():
        if count < 2:
            continue
        digest = hashlib.sha256(original.encode()).hexdigest()
        width = 8
        alias = prefix + digest[:width]
        while alias in entries and entries[alias] != original:
            if width == 64:
                raise ValueError("reference digest collision")
            width += 4
            alias = prefix + digest[:width]
        forward[original] = alias
        entries[alias] = original
    if not entries or len(entries) > 8192:
        return deepcopy(value)

    def replace(node):
        if isinstance(node, str):
            return forward.get(node, node)
        if isinstance(node, dict):
            return {key: replace(child) for key, child in node.items()}
        if isinstance(node, list):
            return [replace(child) for child in node]
        return node

    packed = {**replace(value), FIELD: {
        "contract": CONTRACT, "prefix": prefix, "entries": entries,
        "instruction": "Each @r-style token is the exact full identifier in entries. "
        "It grants no authority. In JSON reference fields you may copy either the "
        "token or the full identifier; keep human-readable prose free of tokens.",
    }}
    if len(_wire(packed).encode()) >= len(_wire(value).encode()):
        return deepcopy(value)
    if expand_reference_view(packed) != value:
        raise ValueError("reference presentation changed its original value")
    return packed


def expand_reference_values(value, dictionary):
    if dictionary is None:
        return deepcopy(value)
    if not isinstance(dictionary, dict) or dictionary.get("contract") not in {CONTRACT, BINDINGS_CONTRACT}:
        raise ValueError("unknown reference wire contract")
    prefix, entries = dictionary.get("prefix"), dictionary.get("entries")
    if (not isinstance(prefix, str) or not re.fullmatch(r"@+r:", prefix)
            or not isinstance(entries, dict) or not entries or len(entries) > 8192):
        raise ValueError("invalid reference dictionary")
    originals = set()
    ordinal = dictionary["contract"] == BINDINGS_CONTRACT
    if ordinal and set(entries) != {prefix + str(index) for index in range(len(entries))}:
        raise ValueError("reference ordinals must be a complete request-local sequence")
    for alias, original in entries.items():
        if (not isinstance(alias, str) or not alias.startswith(prefix)
                or not _opaque(original) or prefix in original or original in originals):
            raise ValueError("invalid or nonunique reference dictionary entry")
        suffix = alias[len(prefix):]
        if not ordinal and (len(suffix) not in range(8, 65, 4) or hashlib.sha256(original.encode()).hexdigest()[:len(suffix)] != suffix):
            raise ValueError("reference alias does not bind its exact identifier")
        originals.add(original)

    def expand(node):
        if isinstance(node, str) and prefix in node:
            if node not in entries:
                raise ValueError("unresolved reference alias")
            return entries[node]
        if isinstance(node, dict):
            return {key: expand(child) for key, child in node.items()}
        if isinstance(node, list):
            return [expand(child) for child in node]
        return deepcopy(node)

    return expand(value)


def reference_bindings_hash(dictionary):
    return hashlib.sha256(json.dumps(dictionary, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _reference_field(key):
    return key in {"ref", "id", "token_map"} or key.endswith(
        ("_ref", "_refs", "_id", "_ids")
    )


_HOST_IDENTITY_PATHS = frozenset({
    ("capability_manifest", "payload_hash"),
    ("inner_life_snapshot", "snapshot_hash"),
})


def _typed_rows(node, key, value):
    columns = node.get("columns")
    if (key not in {"rows", "stable_rows", "volatile_last_row"}
            or not isinstance(columns, list) or not columns
            or any(not isinstance(column, str) for column in columns)
            or len(set(columns)) != len(columns)):
        return None
    single = key == "volatile_last_row"
    rows = [value] if single else value
    if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
        return None
    return columns, rows, single


def prepare_reference_view(value):
    """Return provider values and host-only bindings for typed opaque fields.

    Text/summary/content strings are never interned, even if they resemble an
    identifier. The complete map is retained in the local invocation identity;
    the provider receives its digest and exact selectable handles only.
    """
    if FIELD in value:
        raise ValueError("reference dictionary field already belongs to the input")
    literals = []
    selected = {}

    def collect(node, reference=False, path=()):
        if isinstance(node, str):
            literals.append(node)
            if reference and _opaque(node):
                selected.setdefault(node, None)
        elif isinstance(node, dict):
            literals.extend(node)
            for key, child in node.items():
                child_path = (*path, key)
                table = _typed_rows(node, key, child)
                if table is not None:
                    columns, rows, _single = table
                    for row in rows:
                        for column, cell in zip(columns, row, strict=True):
                            collect(cell, _reference_field(column), (*child_path, "[]", column))
                    continue
                collect(child, _reference_field(key) or child_path in _HOST_IDENTITY_PATHS, child_path)
        elif isinstance(node, list):
            for child in node:
                collect(child, reference, (*path, "[]"))

    # A same-role correction quotes rejected wire bytes, which may contain
    # this request's aliases. Keep those literal records outside the codec:
    # they are not new source values and must not renumber the original pin.
    protected = {key: deepcopy(child) for key, child in value.items() if key in {"correction", "role_result_correction"}}
    core = {key: child for key, child in value.items() if key not in protected}
    collect(core)
    if not selected or len(selected) > 8192:
        return deepcopy(value), None
    prefix = "@r:"
    while any(prefix in literal for literal in literals):
        prefix = "@" + prefix
    entries = {}
    for index, original in enumerate(selected):
        # Opaque source changes must not invalidate the provider's schema
        # prefix. The exact per-request map (including ordinals) is retained
        # in the invocation identity and bound by its digest below.
        alias = prefix + str(index)
        selected[original] = alias
        entries[alias] = original

    def replace(node, reference=False, path=()):
        if isinstance(node, str):
            return selected.get(node, node) if reference else node
        if isinstance(node, dict):
            mapped = {}
            for key, child in node.items():
                child_path = (*path, key)
                table = _typed_rows(node, key, child)
                if table is not None:
                    columns, rows, single = table
                    transformed = [[replace(cell, _reference_field(column), (*child_path, "[]", column))
                                    for column, cell in zip(columns, row, strict=True)] for row in rows]
                    mapped[key] = transformed[0] if single else transformed
                else:
                    mapped[key] = replace(child, _reference_field(key) or child_path in _HOST_IDENTITY_PATHS, child_path)
            return mapped
        if isinstance(node, list):
            return [replace(child, reference, (*path, "[]")) for child in node]
        return deepcopy(node)

    bindings = {"contract": BINDINGS_CONTRACT, "prefix": prefix, "entries": entries}
    view = {**replace(core), **protected, FIELD: {
        "contract": ORDINAL_CONTRACT, "prefix": prefix,
        "bindings_sha256": reference_bindings_hash(bindings),
        "instruction": "@r-style values are exact request-scoped identifiers. Copy offered "
        "identifiers literally in reference fields; their source content and permissions "
        "are supplied alongside them. The host resolves them; they add no authority. "
        "Do not put identifier tokens into human-readable prose.",
    }}
    if len(_wire(view).encode()) >= len(_wire(value).encode()):
        return deepcopy(value), None
    if expand_reference_view(view, bindings) != value:
        raise ValueError("host reference presentation changed its original value")
    return view, bindings


def reference_schema(schema, dictionary):
    """Encode exact offered enum/const values and permit handles on string patterns.

    This is a wire grammar only. After request-bound expansion the unchanged
    canonical validators enforce the original pattern and source authority.
    """
    if dictionary is None:
        return deepcopy(schema)
    from .character_interior.schema_nodes import map_schema_children

    forward = {value: key for key, value in dictionary["entries"].items()}
    prefix = dictionary["prefix"]

    def visit(node):
        if not isinstance(node, dict):
            return deepcopy(node)
        result = map_schema_children(node, visit)
        if isinstance(result.get("enum"), list):
            result["enum"] = [forward.get(v, v) if isinstance(v, str) else v for v in result["enum"]]
        if isinstance(result.get("const"), str):
            result["const"] = forward.get(result["const"], result["const"])
        if result.get("type") == "string" and isinstance(result.get("pattern"), str):
            suffix = "[0-9]{1,4}" if dictionary["contract"] == BINDINGS_CONTRACT else "[0-9a-f]{8,64}"
            result["pattern"] = "(?:" + result["pattern"] + ")|(?:^" + re.escape(prefix) + suffix + "$)"
        return result

    return visit(schema)


def expand_reference_view(value, dictionary=None):
    if not isinstance(value, dict) or FIELD not in value:
        return deepcopy(value)
    marker = value[FIELD]
    if isinstance(marker, dict) and marker.get("contract") in {HOST_CONTRACT, ORDINAL_CONTRACT}:
        if (dictionary is None or marker.get("bindings_sha256") != reference_bindings_hash(dictionary)
                or marker.get("prefix") != dictionary.get("prefix")
                or dictionary.get("contract") != (BINDINGS_CONTRACT if marker["contract"] == ORDINAL_CONTRACT else CONTRACT)):
            raise ValueError("host reference bindings do not match the pinned request")
        protected = {key: deepcopy(child) for key, child in value.items() if key in {"correction", "role_result_correction"}}
        expanded = expand_reference_values(
            {key: child for key, child in value.items() if key != FIELD and key not in protected}, dictionary,
        )
        return {**expanded, **protected}
    else:
        dictionary = marker
    return expand_reference_values({key: child for key, child in value.items() if key != FIELD}, dictionary)


def expand_inbound_arguments(raw, dictionary):
    """Restore identities before the slim parser interprets a chosen capability.

    Keep the original raw bytes separately for the provider audit. Duplicate
    keys or unknown aliases are errors, not a request to salvage the output.
    """
    if dictionary is None:
        return raw

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate inbound reference carrier field')
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=unique)
    string_payload = isinstance(value, dict) and isinstance(value.get('payload_json'), str)
    if string_payload:
        value['payload_json'] = json.loads(value['payload_json'], object_pairs_hook=unique)
    value = expand_reference_values(value, dictionary)
    if string_payload:
        value['payload_json'] = _wire(value['payload_json'])
    return _wire(value)
