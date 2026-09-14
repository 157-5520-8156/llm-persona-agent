"""Lossless, local-only factoring for the documented DeepSeek strict dialect.

Only schema positions are visited; enum/default/example objects are literal data.
The provider uses singular ``$def``. This is opt-in transport compression, not a
change to the canonical role grammar or evidence authority.
"""

from collections import Counter
from copy import deepcopy
import json


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _children(node, visit):
    result = deepcopy(node)
    for key in ("properties", "patternProperties", "dependentSchemas"):
        if isinstance(node.get(key), dict):
            result[key] = {name: visit(child) for name, child in node[key].items()}
    for key in ("items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames", "unevaluatedProperties", "unevaluatedItems"):
        if isinstance(node.get(key), dict):
            result[key] = visit(node[key])
    for key in ("anyOf", "allOf", "oneOf", "prefixItems"):
        if isinstance(node.get(key), list):
            result[key] = [visit(child) for child in node[key]]
    return result


def expand_local_schema_references(schema: dict) -> dict:
    """Expand our closed registry, rejecting remote, unresolved or cyclic refs."""
    definitions = schema.get("$def", {})
    if not isinstance(definitions, dict):
        raise ValueError("local schema definitions must be an object")

    def expand(node, active=()):
        if not isinstance(node, dict):
            return deepcopy(node)
        if "$ref" in node:
            ref = node["$ref"]
            if set(node) != {"$ref"} or not isinstance(ref, str) or not ref.startswith("#/$def/"):
                raise ValueError("only standalone local schema references are supported")
            name = ref.removeprefix("#/$def/")
            if name not in definitions or name in active:
                raise ValueError("unresolved or cyclic local schema reference")
            return expand(definitions[name], (*active, name))
        return _children(node, lambda child: expand(child, active))

    return expand({key: value for key, value in schema.items() if key != "$def"})


def factor_local_schema_references(schema: dict) -> dict:
    """Factor exact repeated schemas; always preserve the expanded input bytes."""
    counts = Counter()

    def count(node):
        if isinstance(node, dict):
            if any(key in node for key in ("$ref", "$def", "$defs", "definitions", "$id", "$anchor", "$dynamicRef", "$dynamicAnchor")):
                raise ValueError("factoring requires an inline schema without reference scopes")
            counts[_wire(node)] += 1
            _children(node, count)
        return node

    count(schema)
    names = {
        wire: f"s{index}" for index, wire in enumerate(sorted(
            wire for wire, count in counts.items() if count > 1 and len(wire.encode()) >= 160
        ))
    }
    definitions = {}

    def replace(node):
        if not isinstance(node, dict):
            return deepcopy(node)
        name = names.get(_wire(node))
        if name is None:
            return _children(node, replace)
        if name not in definitions:
            definitions[name] = _children(node, replace)
        return {"$ref": f"#/$def/{name}"}

    result = _children(schema, replace)
    # A duplicated parent can make its duplicated children single-use. Inline
    # those definitions rather than paying for an unnecessary registry entry.
    while definitions:
        uses = Counter()

        def references(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    uses[node["$ref"].removeprefix("#/$def/")] += 1
                _children(node, references)
            return node

        references(result)
        for node in definitions.values():
            references(node)
        redundant = {name for name in definitions if uses[name] <= 1}
        if not redundant:
            break

        def inline(node):
            if not isinstance(node, dict):
                return deepcopy(node)
            if "$ref" in node and node["$ref"].removeprefix("#/$def/") in redundant:
                return inline(definitions[node["$ref"].removeprefix("#/$def/")])
            return _children(node, inline)

        result = inline(result)
        definitions = {name: inline(node) for name, node in definitions.items() if name not in redundant}
    if definitions:
        result["$def"] = definitions
    if expand_local_schema_references(result) != schema:
        raise ValueError("local schema factoring changed the expanded contract")
    return result if len(_wire(result).encode()) < len(_wire(schema).encode()) else deepcopy(schema)
