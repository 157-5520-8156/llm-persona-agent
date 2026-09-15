"""Map JSON Schema child positions without interpreting literal JSON data."""
from copy import deepcopy


def map_schema_children(node, visit, *, union_visit=None):
    result = deepcopy(node)
    for key in ("properties", "patternProperties", "dependentSchemas"):
        if isinstance(node.get(key), dict):
            result[key] = {name: visit(child) for name, child in node[key].items()}
    for key in ("items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames", "unevaluatedProperties", "unevaluatedItems"):
        if isinstance(node.get(key), dict):
            result[key] = visit(node[key])
    for key in ("anyOf", "allOf", "oneOf", "prefixItems"):
        if isinstance(node.get(key), list):
            result[key] = [(union_visit or visit)(child) if key in {"anyOf", "allOf", "oneOf"} else visit(child) for child in node[key]]
    return result

