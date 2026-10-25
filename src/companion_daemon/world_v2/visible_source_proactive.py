"""Explicit proactive original-capability and completed-output bindings.

These versioned carriers extend the whole-visible gate to one named purpose.
They grant no authority to other StructuredRole purposes or historical turns.
"""

from __future__ import annotations

import base64
import binascii
import json
import zlib

from .deliberation import (
    MAX_COMPLETED_INBOUND_OUTPUT_BYTES,
    MAX_MODEL_OUTPUT_BYTES,
    ModelInput,
    ModelOutput,
)
from .visible_source_runtime import canonical, digest, requirement_table

CAPABILITY_CONTRACT = "character-interior-proactive-capability.2"
CAPABILITY_PREFIX = "proactive-reviewed-turn-capability:sha256:"
DECISION_CONTRACT = "character-interior-proactive-reviewed-decision.1"
REQUIREMENT_KEY = "visible_source_requirement_json"
MAX_COMPLETED_OUTPUT_BYTES = MAX_COMPLETED_INBOUND_OUTPUT_BYTES
_REQUIREMENT_CODEC = "proactive-private-requirement-zlib.1"
_MAX_REQUIREMENT_CHARACTERS = 512_000
_MAX_REQUIREMENT_BYTES = 4 * _MAX_REQUIREMENT_CHARACTERS
_MAX_CAPABILITY_CHARACTERS = 262_144


def _requirement_carrier(*, payload, requirement):
    """Pack only host-private evidence when its escaped copy overflows.

    The role never sees this field. Preserve its exact UTF-8 bytes, rather
    than summarizing sources or widening the shared capability envelope.
    Existing small capabilities retain their historical representation.
    """
    if not isinstance(requirement, str) or len(requirement) > _MAX_REQUIREMENT_CHARACTERS:
        raise ValueError("proactive requirement exceeds its original envelope")
    if len(canonical({**payload, REQUIREMENT_KEY: requirement})) <= _MAX_CAPABILITY_CHARACTERS:
        return requirement
    return _pack_requirement(requirement)


def _pack_requirement(requirement):
    raw = requirement.encode("utf-8")
    return {
        "contract": _REQUIREMENT_CODEC,
        "utf8_bytes": len(raw),
        "sha256": digest(requirement),
        "data": base64.b64encode(zlib.compress(raw, level=6)).decode("ascii"),
    }


def _read_requirement(carrier):
    if isinstance(carrier, str):
        return carrier
    if (not isinstance(carrier, dict)
        or set(carrier) != {"contract", "utf8_bytes", "sha256", "data"}
        or carrier["contract"] != _REQUIREMENT_CODEC
        or type(carrier["utf8_bytes"]) is not int
        or not 0 < carrier["utf8_bytes"] <= _MAX_REQUIREMENT_BYTES
        or not isinstance(carrier["data"], str)
        or len(carrier["data"]) > _MAX_CAPABILITY_CHARACTERS):
        raise ValueError("invalid proactive private requirement carrier")
    try:
        compressed = base64.b64decode(carrier["data"], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, carrier["utf8_bytes"] + 1)
        if (len(raw) != carrier["utf8_bytes"] or not decoder.eof
            or decoder.unconsumed_tail or decoder.unused_data):
            raise ValueError("proactive private requirement stream is incomplete or oversized")
        requirement = raw.decode("utf-8")
    except (binascii.Error, UnicodeError, zlib.error) as exc:
        raise ValueError("invalid proactive private requirement encoding") from exc
    if len(requirement) > _MAX_REQUIREMENT_CHARACTERS or digest(requirement) != carrier["sha256"]:
        raise ValueError("proactive private requirement content differs from its binding")
    return requirement


def qualify_capability(*, payload, request, world_id, actor_ref):
    requirement = request.visible_source_requirement_json
    if requirement is None:
        raise ValueError("proactive visible requirement is missing from the original request")
    pin = requirement_table(requirement).as_dict()["pin"]
    if (pin["world_id"], pin["actor_ref"]) != (world_id, actor_ref):
        raise ValueError("proactive visible requirement belongs to another subject")
    _verify_participant_binding(request=request, payload=payload)
    full = {
        **payload,
        "contract": CAPABILITY_CONTRACT,
        "world_id": world_id,
        "actor_ref": actor_ref,
    }
    return {**full, REQUIREMENT_KEY: _requirement_carrier(payload=full, requirement=requirement)}


def qualified_input(manifest):
    payload = json.loads(manifest.payload_json)
    if payload.get("contract") != CAPABILITY_CONTRACT:
        raise ValueError("required proactive author lacks its reviewed capability")
    requirement = _read_requirement(payload.get(REQUIREMENT_KEY))
    requirement_table(requirement)
    if manifest.capability_ref != CAPABILITY_PREFIX + digest(canonical(payload)):
        raise ValueError("proactive capability does not bind its original requirement")
    original = ModelInput.model_validate_json(json.loads(requirement)["original_input_json"])
    _verify_participant_binding(request=original, payload=payload)
    return original.model_copy(update={REQUIREMENT_KEY: requirement})


def _verify_participant_binding(*, request, payload):
    binding = request.visible_review_participants
    if binding is not None and binding.counterpart_actor_ref != payload.get("counterpart_ref"):
        raise ValueError("proactive counterpart differs from original review participant binding")


def verify_original_capability(*, requirement, lineage, author_request_json):
    """Join immutable Core capability and exact author body to the original pin."""
    from .proactive_action import _proactive_source_frame
    from .visible_source_author_request import verify_visible_source_author_request

    if lineage is None or lineage.purpose != "proactive_contact":
        raise ValueError("reviewed proactive evidence requires its original purpose")
    verify_visible_source_author_request(
        author_request_json,
        expected_request_hash=lineage.author_request_hash.removeprefix("sha256:"),
    )
    carrier = json.loads(author_request_json)
    if carrier["contract"] != "visible-source-proactive-author-request.1":
        raise ValueError("proactive review requires the proactive author body contract")
    from .reference_wire import expand_reference_view

    user = expand_reference_view(
        json.loads(carrier["messages"][1]["content"]),
        (carrier["identity_extras"] or {}).get("reference_bindings"),
    )
    manifest = user["capability_manifest"]
    payload = manifest["payload"]
    if REQUIREMENT_KEY in payload or payload.get("contract") != CAPABILITY_CONTRACT:
        raise ValueError("proactive author body has an invalid private requirement view")
    original = ModelInput.model_validate_json(json.loads(requirement)["original_input_json"])
    _verify_participant_binding(request=original, payload=payload)
    pin = requirement_table(requirement).as_dict()["pin"]
    full = {**payload, REQUIREMENT_KEY: _requirement_carrier(payload=payload, requirement=requirement)}
    capability_hash = digest(canonical(full))
    expected_refs = list(dict.fromkeys(item.ref_id for item in original.trigger_evidence))
    if (
        manifest["capability_ref"] != lineage.capability_ref
        or lineage.capability_ref != CAPABILITY_PREFIX + capability_hash
        or manifest["payload_hash"] != "sha256:" + capability_hash
        or manifest["capability_kind"] != "proactive_contact"
        or manifest["source_refs"] != expected_refs
        or original.trigger_ref not in expected_refs
        or payload["source_opportunity"] != _proactive_source_frame(original.model_content_json)
        or payload["world_id"] != pin["world_id"]
        or payload["actor_ref"] != pin["actor_ref"]
        or lineage.causal_world_id != pin["world_id"]
        or lineage.causal_actor_ref != pin["actor_ref"]
        or set(lineage.causal_source_refs) != set(expected_refs)
        or lineage.causal_epoch != original.trigger_ref
        or user["inner_turn"]["inner_turn_id"] != lineage.inner_turn_id
        or user["inner_turn"]["phase"] != "consider"
        or user["inner_turn"]["trigger_ref"] != original.trigger_ref
        or user["inner_turn"]["subject_ref"] != lineage.opportunity_ref
    ):
        raise ValueError("proactive visible review escaped its original capability")
    return original


def record_output(output):
    material = output.model_dump(mode="json")
    for name in (
        "provider_subcall_audits",
        "authored_candidate_audits",
        "physical_provider_audits",
    ):
        material[name] = [item.model_dump(mode="json") for item in getattr(output, name)]
    raw = canonical(material)
    if len(raw.encode()) > MAX_COMPLETED_OUTPUT_BYTES:
        raise ValueError("reviewed proactive completed output exceeds its byte bound")
    return {"output_json": raw, "output_hash": digest(raw)}


def restore_output(*, decision, request):
    from .visible_source_runtime import verify_output

    value = decision.decision
    if value.get("contract") != DECISION_CONTRACT:
        raise ValueError("required proactive turn has no durable reviewed result")
    raw = value["output_json"]
    if len(raw.encode()) > MAX_COMPLETED_OUTPUT_BYTES or digest(raw) != value["output_hash"]:
        raise ValueError("proactive completed output changed bytes")
    output = ModelOutput.model_validate_json(raw)
    author = decision.author_lineage
    if (
        author is None
        or output.winning_model_call_id != author.model_call_id
        or output.winning_request_hash != author.request_hash.removeprefix("sha256:")
        or output.model_id != author.model_id
        or output.model_version != author.model_version
    ):
        raise ValueError("proactive completed output differs from its Core author")
    verify_output(request=request, output=output)
    return output
