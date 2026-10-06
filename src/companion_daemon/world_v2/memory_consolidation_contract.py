"""Character-owned periodic retention decisions, not automatic forgetting."""
from typing import Literal
from copy import deepcopy
import json

from pydantic import Field, model_validator

from .schema_core import FrozenModel

PURPOSE = "memory_consolidation"
CONTRACT = "character-interior-memory-consolidation.1"
HOST_SOURCE_BINDING = "memory-review-host-source-binding.1"
UNDERSTANDING_CONTRACT = "source-bound-memory-understanding.1"


class MemoryEvidenceQuote(FrozenModel):
    source_token: str = Field(min_length=1, max_length=32)
    quote: str = Field(min_length=1, max_length=240)


class MemoryUnderstandingDraft(FrozenModel):
    """A role-owned interpretation; never a new Fact or emotion command."""

    operation: Literal["retain", "revise", "merge", "release"]
    predecessor_tokens: tuple[str, ...] = Field(max_length=4)
    # Read old receipts unchanged. New wire chooses sources once, in quotes.
    source_tokens: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=4,
                                                exclude_if=lambda value: value is None)
    facet: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=700)
    confidence_bp: int = Field(ge=0, le=10000)
    evidence_quotes: tuple[MemoryEvidenceQuote, ...] = Field(min_length=1, max_length=4)

    @property
    def selected_source_tokens(self):
        return self.source_tokens or tuple(dict.fromkeys(q.source_token for q in self.evidence_quotes))

    @model_validator(mode="after")
    def explicit_lifecycle(self):
        if len(set(self.selected_source_tokens)) != len(self.selected_source_tokens) or len(set(self.predecessor_tokens)) != len(self.predecessor_tokens):
            raise ValueError("understanding selections must be unique")
        count = len(self.predecessor_tokens)
        if ((self.operation == "retain" and count != 0)
                or (self.operation in {"revise", "release"} and count != 1)
                or (self.operation == "merge" and count < 2)):
            raise ValueError("understanding operation does not match its predecessors")
        if {q.source_token for q in self.evidence_quotes} != set(self.selected_source_tokens):
            raise ValueError("each understanding source needs one exact evidence quote")
        return self


class MemoryReviewChoice(FrozenModel):
    candidate_token: str = Field(min_length=1, max_length=32)
    disposition: Literal["retain", "forget"]
    next_review_hours: int = Field(ge=24, le=720)


class MemoryReviewBatch(FrozenModel):
    contract: Literal["character-interior-memory-consolidation.1"] = CONTRACT
    choices: tuple[MemoryReviewChoice, ...] = Field(min_length=1, max_length=8)
    # Omitted on immutable choice-only jobs/results. Explicit [] on new jobs
    # means the character saw the opportunity and chose no new understanding.
    understandings: tuple[MemoryUnderstandingDraft, ...] | None = Field(
        default=None, max_length=3, exclude_if=lambda value: value is None,
    )


def validate_payload(payload, offered, *, source_tokens=None, predecessor_tokens=None, readings=None):
    batch = MemoryReviewBatch.model_validate_json(json.dumps(dict(payload)), strict=True)
    tokens = [c.candidate_token for c in batch.choices]
    if len(set(tokens)) != len(tokens) or set(tokens) != set(offered):
        raise ValueError(
            "periodic memory review must choose once for each offered candidate; "
            f"missing={sorted(set(offered) - set(tokens))}, "
            f"unoffered={sorted(set(tokens) - set(offered))}, "
            f"duplicates={sorted({token for token in tokens if tokens.count(token) > 1})}"
        )
    retired = set()
    forgotten = {c.candidate_token for c in batch.choices if c.disposition == "forget"}
    for index, update in enumerate(batch.understandings or ()):
        if retired.intersection(update.predecessor_tokens):
            raise ValueError("one understanding cannot be replaced twice in a batch")
        retired.update(update.predecessor_tokens)
        if (source_tokens is not None and not set(update.selected_source_tokens).issubset(source_tokens)):
            raise ValueError(f"understandings[{index}] selected unoffered memory sources "
                             f"{sorted(set(update.selected_source_tokens) - set(source_tokens))}; "
                             f"choose only {sorted(source_tokens)}, not general attention s-tokens")
        if predecessor_tokens is not None and not set(update.predecessor_tokens).issubset(predecessor_tokens):
            raise ValueError("understanding selected an unoffered predecessor")
        if forgotten.intersection(update.selected_source_tokens):
            raise ValueError(f"understandings[{index}] cannot reactivate a memory forgotten in this batch: "
                             f"{sorted(forgotten.intersection(update.selected_source_tokens))}. "
                             "Choose yourself: retain those candidate sources, or remove/reselect this understanding. "
                             "The host will not change either choice.")
        if readings is not None:
            for quote_index, evidence in enumerate(update.evidence_quotes):
                if not any(evidence.quote in text for text in source_texts(readings[evidence.source_token])):
                    raise ValueError(
                        f"understandings[{index}].evidence_quotes[{quote_index}].quote does not "
                        f"occur in the exact memory source prose for {evidence.source_token}. "
                        "Copy one contiguous passage from that token's supplied source text, "
                        "or reselect the source yourself; keep paraphrases in summary."
                    )


def _source_prose_fields(reading):
    """Addresses of authored prose only, shared by quoting and validation."""
    for excerpt in reading["source_excerpts"]:
        if excerpt.get("text"):
            yield excerpt, "text"
        world = excerpt.get("world_consequence") or {}
        for field in ("environment", "authorized_attempt_result"):
            if (world.get(field) or {}).get("text"):
                yield world[field], "text"
        if (excerpt.get("character_response") or {}).get("response_text"):
            yield excerpt["character_response"], "response_text"


def source_texts(reading):
    """Only authored source prose, never IDs, scores or metadata strings."""
    return tuple(owner[key] for owner, key in _source_prose_fields(reading))


def quote_handle_view(capability_payload):
    """Lossless source prose presentation with explicitly selectable quote IDs.

    Fixed character windows constrain the existing 240-character quote field,
    not the role's interpretation. All prose remains in order and is shown
    once. No summary, inferred sentence or unrelated metadata becomes evidence.
    """
    view = deepcopy(dict(capability_payload))
    options = {}
    for row in [*view.get("memories", []), *view.get("related_memories", [])]:
        number = 0
        for owner, key in _source_prose_fields(row["reading"]):
            text = owner[key]
            refs = []
            for start in range(0, len(text), 240):
                ref = f"{row['token']}.q{number}"
                if ref in options:
                    raise ValueError("duplicate memory quote source token")
                options[ref] = {"source_token": row["token"], "text": text[start:start + 240]}
                refs.append(ref)
                number += 1
            owner[key] = {"quote_parts": refs}
    view["quote_options"] = options
    return view


def expand_quote_handles(payload, *, capability_payload):
    """Restore only the exact source and text the role selected by handle."""
    result = deepcopy(payload)
    options = quote_handle_view(capability_payload)["quote_options"]
    for update in result.get("understandings") or ():
        if not isinstance(update, dict):
            continue  # Canonical validation reports the malformed update.
        quotes = update.get("evidence_quotes")
        if not isinstance(quotes, list):
            continue
        expanded = []
        for quote in quotes:
            if not isinstance(quote, dict) or "quote_ref" not in quote:
                expanded.append(quote)  # Historical canonical results remain readable.
                continue
            if set(quote) != {"quote_ref"} or not isinstance(quote["quote_ref"], str):
                raise ValueError("memory quote handle cannot override its source or text")
            option = options.get(quote["quote_ref"])
            if option is None:
                raise ValueError("unoffered quote handle: " + quote["quote_ref"])
            expanded.append({"source_token": option["source_token"], "quote": option["text"]})
        update["evidence_quotes"] = expanded
    return result


def expand_understanding_dispositions(payload):
    """Decode selected predecessors and retain/release into canonical operations.

    Every valid old operation has one representation in this transport. No
    predecessor, quote, interpretation or confidence is chosen by the host.
    """
    result = dict(payload)
    updates = result.get("understandings")
    if not isinstance(updates, list):
        return result
    expanded = []
    for update in updates:
        if not isinstance(update, dict) or "disposition" not in update:
            raise ValueError("memory understanding requires its transport disposition")
        if "operation" in update:
            raise ValueError("memory understanding cannot own both operation and disposition")
        disposition = update["disposition"]
        predecessors = update.get("predecessor_tokens")
        if not isinstance(predecessors, list):
            raise ValueError("memory understanding predecessors must be an explicit list")
        if disposition == "release":
            operation = "release"
        elif disposition == "retain":
            operation = "retain" if not predecessors else "revise" if len(predecessors) == 1 else "merge"
        else:
            raise ValueError("memory understanding disposition is unavailable")
        expanded.append({**{key: value for key, value in update.items() if key != "disposition"},
                         "operation": operation})
    return {**result, "understandings": expanded}


def expand_named_choices(payload, *, offered_tokens):
    """Decode named choices without adding, dropping or deciding a choice."""
    choices = payload.get("choices")
    if not isinstance(choices, dict):
        return payload  # Historical list-shaped results retain their canonical checks.
    if set(choices) != set(offered_tokens):
        raise ValueError("named memory choices must match the offered tokens exactly; "
                         f"missing={sorted(set(offered_tokens) - set(choices))}, "
                         f"unoffered={sorted(set(choices) - set(offered_tokens))}")
    if any(not isinstance(value, dict) or "candidate_token" in value for value in choices.values()):
        raise ValueError("each named memory choice must be an object with no duplicate token field")
    return {**payload, "choices": [
        {"candidate_token": token, **choices[token]} for token in offered_tokens
    ]}


def tool_contract(*, capability_payload, source_refs, recall_allowed, compact_synthesis_transport=False,
                  named_choices=False, quote_handles=False):
    from .character_interior.structured_role_tool_contract import (
        _compile_generic_decision_contract, _provider_schema,
    )

    schema = _provider_schema(MemoryReviewBatch)
    synthesis = capability_payload.get("understanding_contract") == UNDERSTANDING_CONTRACT
    host_bound = capability_payload.get("source_binding") == HOST_SOURCE_BINDING
    compact_synthesis_transport = bool(compact_synthesis_transport and synthesis and host_bound)
    named_choices = bool(named_choices and compact_synthesis_transport)
    quote_handles = bool(quote_handles and named_choices)
    if named_choices:
        offered = capability_payload.get("offered_tokens")
        if (not isinstance(offered, list) or not 1 <= len(offered) <= 8
                or offered != [f"m{i}" for i in range(len(offered))]):
            raise ValueError("named memory choices need the installed ordered batch tokens")
        item = schema["properties"]["choices"]["items"]
        item["properties"].pop("candidate_token")
        item["required"] = [key for key in item["required"] if key != "candidate_token"]
        schema["properties"]["choices"] = {
            "type": "object", "properties": {token: item for token in offered},
            "required": list(offered), "additionalProperties": False,
        }
    if not synthesis:
        schema["properties"].pop("understandings", None)
        schema["required"] = [key for key in schema.get("required", []) if key != "understandings"]
    else:
        # Provider-side enum constraints offer exact source/standing-view
        # tokens. Semantic grouping and the decision to merge remain role-owned.
        update_schema = schema["properties"]["understandings"]
        if "anyOf" in update_schema:
            update_schema = next(item for item in update_schema["anyOf"] if item.get("type") == "array")
        schema["properties"]["understandings"] = update_schema
        if "understandings" not in schema["required"]:
            schema["required"].append("understandings")
        item = update_schema["items"]
        item["properties"].pop("source_tokens",None)
        item["required"] = [key for key in item["required"] if key != "source_tokens"]
        predecessors = list(capability_payload["understanding_tokens"])
        if compact_synthesis_transport:
            # Exact membership and quotes remain mandatory in validate_payload.
            # Offered handles live in the pinned capability, not a changing
            # provider schema. This transport cannot authorize any new source.
            item["properties"].pop("operation")
            item["properties"]["disposition"] = {"type": "string", "enum": ["retain", "release"]}
            item["required"] = ["disposition" if key == "operation" else key for key in item["required"]]
        elif predecessors:
            item["properties"]["predecessor_tokens"]["items"]["enum"] = predecessors
        else:
            item["properties"]["predecessor_tokens"]["maxItems"] = 0
        item["properties"]["evidence_quotes"]["items"]["properties"]["source_token"]["enum"] = list(capability_payload["understanding_source_tokens"])
        if quote_handles:
            item["properties"]["evidence_quotes"]["items"] = {
                "type": "object", "properties": {"quote_ref": {"type": "string"}},
                "required": ["quote_ref"], "additionalProperties": False,
            }
    schema["properties"].pop("contract", None)
    schema["required"] = [key for key in schema.get("required", []) if key != "contract"]
    return _compile_generic_decision_contract(
        purpose=PURPOSE, tool_name=("character_memory_consolidation_v6" if quote_handles
                                   else "character_memory_consolidation_v5" if named_choices
                                   else "character_memory_consolidation_v4" if host_bound and synthesis and compact_synthesis_transport
                                   else "character_memory_consolidation_v2" if host_bound
                                   else "character_memory_consolidation_v1"),
        payload_schema=schema, capability_identity=capability_payload,
        source_refs=source_refs, recall_allowed=recall_allowed,
        description=("Return exactly one choice per offered memory token, without extra or duplicate choices; "
                     "elapsed time never forces forgetting. "
                     "When standing understandings are offered, update that existing ledger incrementally: "
                     "revise or merge existing aspects, retain only genuinely new aspects, or choose [] if unchanged. "
                     "understandings contains at most 3 updates TOTAL across the whole batch, not 3 per memory or facet. "
                     "Select which updates matter yourself. A source you choose to forget in choices cannot also support "
                     "an understanding in this batch."
                     + (" evidence_quotes must copy an exact contiguous passage from "
                        "that token's supplied source prose; a paraphrase belongs in summary, never in quote."
                        if not quote_handles else "")
                     + (" choices is an object keyed by every offered memory token; choose disposition and "
                        "next_review_hours for every key. Do not add candidate_token inside its value."
                        if named_choices else "")
                     + (" For evidence_quotes select quote_ref from quote_options, never retype the prose. "
                        "quote_parts reconstruct each complete source text in order. The selected handle "
                        "binds exactly that source and its original text; interpretations belong in summary."
                        if quote_handles else "")
                     + (" Use only the exact offered source and predecessor tokens in the pinned capability; "
                        "the host rejects unknown tokens, withdrawn sources and non-exact quotes."
                        if compact_synthesis_transport and synthesis else "")),
        bind_sources_in_host=host_bound,
        expanded_attention=host_bound,
    )
