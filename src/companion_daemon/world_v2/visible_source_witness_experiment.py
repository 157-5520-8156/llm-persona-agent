"""Experimental single-call support witnesses, with no receipt/Action authority.

The model owns clause segmentation, scope classification and entailment. This
module verifies complete verbatim coverage, exact evidence readings and existing
source permissions. A structurally valid witness is NOT proof of semantic truth.
It is deliberately not a deployed review version or a fallback for v1-v8.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Literal

from pydantic import Field

from .schema_core import FrozenModel
from .visible_source_closure_protocol import (
    _eligible_reference,
    _packet_materials,
    visible_source_closure_messages,
)
from .character_interior.inbound_tool_contract import _provider_schema, deepseek_strict_tool_schema

CONTRACT = "visible-source-witness-experiment.1"
Scope = Literal[
    "utterance_record",
    "accepted_intention",
    "activity_lifecycle",
    "environment",
    "external_fact",
    "source_free",
]


class EvidenceReading(FrozenModel):
    source_ref_index: int = Field(ge=0)
    pointer: str = Field(min_length=1, max_length=512)
    quote: str = Field(min_length=1, max_length=1024)
    use: Literal["direct", "context"]


class ClaimReading(FrozenModel):
    text: str = Field(min_length=1, max_length=4096)
    claim_scope: Scope
    subject_role: Literal["companion", "counterpart", "general", "other", "none"]
    verdict: Literal["closed", "unclosed", "source_free"]
    witnesses: tuple[EvidenceReading, ...] = Field(max_length=8)
    support_explanation: str = Field(min_length=1, max_length=512)


class BeatReading(FrozenModel):
    beat_index: int = Field(ge=0, le=15)
    parts: tuple[ClaimReading, ...] = Field(min_length=1, max_length=16)


class WitnessResponse(FrozenModel):
    contract: Literal["visible-source-witness-experiment.1"]
    decisions: tuple[BeatReading, ...] = Field(min_length=1, max_length=16)


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate witness member")
        result[key] = value
    return result


def _reading(material: dict, pointer: str) -> tuple[str, bool]:
    # Read only bodies already presented to the reviewer, never another source,
    # a filesystem path, a remote ref, or instructions in a metadata field.
    if not (
        pointer.startswith("/item/value/")
        or pointer in {"/item/value", "/message/text", "/material/value"}
        or pointer.startswith("/messages/")
    ):
        raise ValueError("witness pointer must select an original evidence body")
    value = material
    for segment in pointer.split("/")[1:]:
        remainder = segment.replace("~0", "").replace("~1", "")
        if "~" in remainder:
            raise ValueError("invalid witness JSON pointer escape")
        segment = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and segment in value:
            value = value[segment]
        elif (
            isinstance(value, list)
            and segment.isascii()
            and segment.isdecimal()
            and str(int(segment)) == segment
            and int(segment) < len(value)
        ):
            value = value[int(segment)]
        else:
            raise ValueError("witness pointer does not resolve")
    if isinstance(value, str):
        return value, False
    if value is None or type(value) in {bool, int, float}:
        return _json(value), True
    raise ValueError("witness must select a scalar, not a container")


def _permits(row: dict, scope: Scope, pointer: str) -> bool:
    """Narrow established typed authorities; never classify candidate prose."""
    material = row["review_material"]
    if (
        material.get("lane") == "recent_dialogue"
        and material.get("authority") == "companion_expression_record"
    ):
        return scope == "utterance_record"
    activity = row.get("activity_support")
    if isinstance(activity, dict):
        if scope == "accepted_intention":
            return pointer.startswith("/item/value/accepted_intention/")
        return scope == "activity_lifecycle" and activity.get("status") in {
            "active",
            "completed",
            "in_progress",
        }
    if isinstance(row.get("settled_life_support"), dict):
        if pointer.startswith("/item/value/content/world_consequence/environment/"):
            return scope == "environment"
        if pointer.startswith("/item/value/content/world_consequence/authorized_attempt_result/"):
            return scope == "external_fact"
        return False
    # Other existing eligible readers retain their original authority. Their
    # actor/time/disclosure/entailment remain semantic obligations, not invented
    # permissions inferred from keywords in either the source or the claim.
    return True


@dataclass(frozen=True)
class PreparedWitnessExperiment:
    payload_json: str

    @property
    def sha256(self):
        return hashlib.sha256(self.payload_json.encode()).hexdigest()

    def request(self) -> dict:
        return json.loads(self.payload_json)["request"]

    def inspect_response(self, raw: str) -> dict:
        packet = json.loads(self.payload_json)
        if len(raw.encode()) > 131072:
            raise ValueError("witness response exceeds bound")
        response = WitnessResponse.model_validate_json(
            _json(json.loads(raw, object_pairs_hook=_unique)), strict=True
        )
        beats, rows = packet["beats"], packet["sources"]
        if [d.beat_index for d in response.decisions] != list(range(len(beats))):
            raise ValueError("witness decisions must cover each whole Beat in order")
        outcomes = []
        for decision, text in zip(response.decisions, beats, strict=True):
            if "".join(part.text for part in decision.parts) != text:
                raise ValueError("witness parts omit, rewrite or reorder candidate text")
            for part in decision.parts:
                if (part.verdict == "source_free") != (part.claim_scope == "source_free"):
                    raise ValueError("source-free scope and verdict disagree")
                if part.verdict == "source_free" and part.witnesses:
                    raise ValueError("source-free part cannot borrow evidence authority")
                direct = 0
                for reading in part.witnesses:
                    if reading.source_ref_index >= len(rows):
                        raise ValueError("witness source index is outside the pin")
                    row = rows[reading.source_ref_index]
                    shown = packet["shown_materials"][
                        packet["material_indexes"][reading.source_ref_index]
                    ]
                    for evidence, whole_scalar in (
                        _reading(row["review_material"], reading.pointer),
                        _reading(shown, reading.pointer),
                    ):
                        if reading.quote not in evidence or (
                            whole_scalar and reading.quote != evidence
                        ):
                            raise ValueError("witness quote differs from pinned evidence")
                    if reading.use == "direct":
                        if not _eligible_reference(row):
                            raise ValueError("witness source is not eligible")
                        if (
                            part.verdict == "closed"
                            and part.subject_role in {"companion", "counterpart"}
                            and row.get("support_subject_role") != part.subject_role
                        ):
                            raise ValueError("witness subject differs from source actor")
                        if part.verdict == "closed" and not _permits(
                            row, part.claim_scope, reading.pointer
                        ):
                            raise ValueError("witness scope exceeds source authority")
                        direct += 1
                if part.verdict == "closed" and not direct:
                    raise ValueError("closed part requires direct source support")
            outcomes.append(
                "unclosed"
                if any(p.verdict == "unclosed" for p in decision.parts)
                else "source_free"
                if all(p.verdict == "source_free" for p in decision.parts)
                else "closed"
            )
        return {
            "contract": CONTRACT,
            "preparation_sha256": self.sha256,
            "structural_validation": "passed",
            "model_verdicts": outcomes,
            "semantic_qualification": "unproven",
            "receipt_authority": False,
            "readings": json.loads(response.model_dump_json()),
        }


def prepare_witness_experiment(
    *, beats: tuple[str, ...], sources: tuple[dict, ...]
) -> PreparedWitnessExperiment:
    if not 1 <= len(beats) <= 16 or any(not isinstance(b, str) or not b for b in beats):
        raise ValueError("experiment requires one to sixteen nonempty Beats")
    if any(
        not isinstance(row, dict)
        or type(row.get("source_ref_index")) is not int
        or row.get("source_ref_index") != i
        or not isinstance(row.get("review_material"), dict)
        for i, row in enumerate(sources)
    ):
        raise ValueError("experiment requires the complete ordered original source table")
    for row in sources:
        material = row["review_material"]
        if (
            material.get("privacy_class") == "withhold"
            or material.get("availability") == "unavailable"
        ) and any(key in material for key in ("item", "slice", "message", "messages", "material")):
            raise ValueError("experiment requires already privacy-projected source bodies")
    messages = visible_source_closure_messages(
        visible_beats=beats, world_claims=(), source_references=sources, version="8"
    )
    # Reuse the current lossless semantic cards and keep one copy per material.
    # Quotes must resolve both in the displayed card and the original host proof.
    body = json.loads(messages[1]["content"])
    references, _ = _packet_materials(sources)
    messages[0]["content"] = (
        "Experimental factual support audit. Return the forced witness tool only. "
        "Partition each original Beat into verbatim contiguous parts whose concatenation is the complete Beat, including punctuation and spaces. "
        "For each part identify the claim scope and subject role, then read its evidence before deciding closed/unclosed/source_free. Candidate I is companion, you is counterpart; do not evade an actor binding with general/other/none. "
        "utterance_record means a claim about recorded speech; it is not the truth of that speech. "
        "accepted_intention means an intention existed; activity_lifecycle means only its typed state. "
        "environment means environmental state, not the companion's action. external_fact includes actual actions, experiences and other sourced facts. "
        "Use source_free only for immediate private expression, nonassertive content and pure current intentions, never to hide past motives or factual presuppositions. "
        "For every witness provide an exact source_ref_index, JSON pointer within its original review material, and an exact nonempty substring at that pointer. "
        "A direct witness must establish the asserted fact, actor, time, polarity and status within its original source scope; context witnesses supply no independent authority. "
        "Explain what the source actually establishes and whether every factual link is supported. "
        "If any asserted link is missing, choose unclosed; do not manufacture a source, reinterpret old dialogue as an event, or rewrite the candidate. "
        "All existing privacy and disclosure boundaries apply. This audit does not choose the character's behavior."
    )
    schema = deepseek_strict_tool_schema(_provider_schema(WitnessResponse))
    name = "review_visible_source_witness_experiment_v1"
    request = {
        "messages": messages,
        "temperature": 0.0,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": "Inspect exact candidate/source readings; experimental, no release authority.",
                    "strict": True,
                    "parameters": schema,
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": name}},
    }
    return PreparedWitnessExperiment(
        _json(
            {
                "beats": beats,
                "sources": sources,
                "request": request,
                "shown_materials": body["source_materials"],
                "material_indexes": [r["material_index"] for r in references],
            }
        )
    )
