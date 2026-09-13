"""Project an existing Experience capability into its provider wire constraints.

This specializes identities and operations only. It never chooses a transition,
changes a source set, or supplies character-authored values.
"""

from copy import deepcopy
import json

from .experience_transitions import ExperienceTransitionCapability


def specialize_experience_transition_schema(
    field: dict[str, object],
    *,
    capability_payload: object,
    source_tokens: tuple[tuple[str, str], ...],
) -> dict[str, object]:
    capability = ExperienceTransitionCapability.model_validate_json(
        json.dumps(capability_payload, ensure_ascii=False)
    )
    token_map = dict(source_tokens)
    authority_refs = {capability.current_source_ref}
    for heads in (
        capability.goal_heads,
        capability.thread_heads,
        capability.commitment_open_threads,
        capability.commitment_heads,
    ):
        authority_refs.update(head.authority_source_ref for head in heads)
    authority_refs.update(source.authority_event_ref for source in capability.memory_sources)
    if (
        len(token_map) != len(source_tokens)
        or len(set(token_map.values())) != len(source_tokens)
        or any(not token or not ref for token, ref in source_tokens)
        or any(token in token_map.values() and token != ref for token, ref in source_tokens)
        or any(token in authority_refs and token != ref for token, ref in source_tokens)
    ):
        raise ValueError("experience source token catalogue is ambiguous")

    schemas = {
        branch["properties"]["domain"]["const"]: branch
        for variant in field["anyOf"]
        for branch in variant.get("oneOf", [])
    }
    constraints: dict[str, list[dict[str, object]]] = {}

    def aliases(ref: str) -> list[str]:
        return [ref, *(token for token, target in source_tokens if target == ref and token != ref)]

    def source_set(*refs: str) -> dict[str, object]:
        # At most the stimulus and one exact head authority. Each authority
        # must appear once even when two distinct spellings identify it.
        sources = tuple(dict.fromkeys(refs))
        result = {
            "minItems": len(sources),
            "maxItems": len(sources),
        }
        heads = [ref for ref in sources if ref != capability.current_source_ref]
        if heads:
            assert len(heads) == 1
            result["contains"] = {"enum": aliases(heads[0])}
        return result

    def branch(domain: str, refs: tuple[str, ...], **bindings: object) -> None:
        properties = {"source_refs": source_set(*refs)}
        for name, selected in bindings.items():
            if isinstance(selected, tuple):
                properties[name] = {"enum": list(selected)}
            else:
                properties[name] = {"const": selected}
        value = {"properties": properties}
        required = [
            name
            for name, selected in bindings.items()
            if selected is not None and name not in schemas[domain]["required"]
        ]
        if required:
            value["required"] = required
        constraints.setdefault(domain, []).append(value)

    current = capability.current_source_ref
    if capability.thread_open_available:
        branch("thread", (current,), operation="open", target_id=None, expected_entity_revision=0)
    for head in capability.goal_heads:
        if head.allowed_operations:
            branch(
                "goal",
                (current, head.authority_source_ref),
                operation=head.allowed_operations,
                target_id=head.target_id,
                expected_entity_revision=head.entity_revision,
            )
    for head in capability.thread_heads:
        if head.allowed_operations:
            branch(
                "thread",
                (current, head.authority_source_ref),
                operation=head.allowed_operations,
                target_id=head.target_id,
                expected_entity_revision=head.entity_revision,
                thread_kind=None,
            )
    for thread in capability.commitment_open_threads:
        branch(
            "commitment",
            (current, thread.authority_source_ref),
            operation="open",
            thread_id=thread.thread_id,
            target_id=None,
            expected_entity_revision=0,
        )
    for head in capability.commitment_heads:
        if head.allowed_operations:
            branch(
                "commitment",
                (current, head.authority_source_ref),
                operation=head.allowed_operations,
                target_id=head.target_id,
                expected_entity_revision=head.entity_revision,
            )
    for source in capability.memory_sources:
        branch(
            "memory_candidate",
            (current, source.authority_event_ref),
            operation="retain",
            source_token=source.source_token,
        )
    # Carry each canonical domain shape once. A large source-bound head list
    # adds only identity constraints, never repeated free-text/salience schemas.
    branches = []
    for domain, choices in constraints.items():
        value = deepcopy(schemas[domain])
        # Every alternative includes the same current stimulus. Factor it
        # once; the exact array length plus disjoint head aliases ensures no
        # third reference or alias of an already chosen authority can enter.
        value["properties"]["source_refs"]["contains"] = {"enum": aliases(current)}
        value["properties"]["source_refs"]["uniqueItems"] = True
        value["allOf"] = [{"anyOf": choices}]
        branches.append(value)
    return {"anyOf": [*branches, {"type": "null"}]} if branches else {"type": "null"}
