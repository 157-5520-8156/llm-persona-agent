"""Versioned wire presentation of the existing expression grammar.

The role contract already requests world_claims before beats. Keep schema
properties in that same order without changing which values are valid or
requiring a particular serialization order from the model's response.
"""
from copy import deepcopy
import hashlib
import json

from .schema_nodes import map_schema_children

CONTRACT = "expression-evidence-before-beats.1"


def evidence_first_expression_schema(schema: dict) -> dict:
    def visit(node):
        if not isinstance(node, dict):
            return deepcopy(node)
        result = map_schema_children(node, visit)
        # Definition registries contain schemas; enum/default/example values
        # are data and are deliberately not traversed.
        for registry in ("$def", "$defs", "definitions"):
            if isinstance(node.get(registry), dict):
                result[registry] = {name: visit(value) for name, value in node[registry].items()}
        properties = result.get("properties")
        if isinstance(properties, dict) and {"beats", "world_claims", "timing_choice"} <= properties.keys():
            result["properties"] = {key: value for key, value in properties.items() if key != "beats"}
            result["properties"]["beats"] = properties["beats"]
        return result

    result = visit(schema)
    # Dict equality ignores key order. Every schema value, array order, branch,
    # field constraint and required member must otherwise stay identical.
    if result != schema:
        raise ValueError("expression presentation changed the role grammar")
    return result


def ordered_schema_sha256(schema: dict) -> str:
    """Ordinary canonical schema hashes intentionally ignore property order."""
    wire = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(wire.encode()).hexdigest()
