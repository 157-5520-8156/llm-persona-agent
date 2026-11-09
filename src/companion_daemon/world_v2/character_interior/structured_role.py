"""One structured, source-bound character author for every interior purpose.

Purpose contracts in this module describe only wire shape and capability
closure.  They do not encode a motive, tone, social policy, or preferred
choice.  Provider and parsing failures remain technical failures; this faculty
never discovers a second author or manufactures a character result.
"""

from __future__ import annotations

from ..memory_consolidation_contract import validate_payload as _validate_periodic_memory

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    field_validator,
    model_validator,
)

from companion_daemon.llm import (
    model_call_scope,
    model_provider_request_identity_scope,
    provider_invocation_request_hash,
)

from ..model_completion import ChatCompletionModel
from ..character_outcome_contract import CharacterLifeDirectionDraft
from ..chat_life_intent_contract import LifeIntentDraft
from ..world_life_intent_contract import WorldPlanResponseDraft
from ..character_life_response_contract import validate_character_life_response_coverage
from .rejected_role_result import RejectedRoleResult, original_role_request, role_request_binding
from .life_source_origin import canonical
from ..proposal_envelope import AspirationTransitionPayload
from ..schema_core import canonicalize_json_value
from ..structured_completion import complete_json_object
from ..schemas import MemoryCueKind, MemoryRetentionRationale
from .single_tool_transport import SingleToolTransport, resolve_single_tool_transport
from .author_identity import character_semantic_author_identity
from .current_life_authorship import AUTHOR_PRESENTATION_CONTRACT, current_life_authorship_authority
from .contracts import (
    InteriorAffectOpenTransition,
    InteriorAffectSupersedeTransition,
    InteriorAffectUpdateTransition,
    InteriorAffectTransition,
    _InteriorAuthorLineage,
    _InteriorCapabilityManifest,
)
from .experience_transitions import (
    ExperienceTransitionCapability,
    ExperienceTransitionDraft,
    validate_experience_transition_draft,
)
from .ports import (
    _InteriorRoleRequest,
    _InteriorRoleResult,
    _RoleResultContractError,
)
from .snapshot_compiler import citeable_source_labels
from ..background_context_profile import (
    REGISTERED_BACKGROUND_PURPOSES,
    background_context_profile_for_purpose,
    profile_audit_record,
    slice_background_inner_life_snapshot,
)
from ..present_prompt import (
    ordered_json_dumps,
    present_inner_life,
)
from .structured_role_tool_contract import (
    StructuredRoleToolContract,
    StructuredRoleToolContracts,
)
from ..pinned_source_ref import (
    PinnedSourceCatalog,
    resolve_pinned_source_ref_list,
    unpinned_source_failure_detail,
)


_FACET_NAMES = (
    "private_self",
    "selective_memory",
    "appraisal_affect",
    "emotional_continuity",
    "subjective_relationship",
    "aspirations_conflicts",
    "autonomous_impulses",
    "expression_stance",
)

_LEGACY_INTERIOR_USER_PAYLOAD_ORDER = (
    "wire_contract", "purpose_contract", "current_authorship_authority",
    "capability_manifest", "background_context_profile", "eight_facets",
    "citeable_sources", "inner_life_snapshot", "selective_recall",
    "purpose_instruction", "inner_turn", "correction",
)

# In the installed reference transport, number references only AFTER stable
# character/environment material. A new capability must not renumber that
# material or put its unique hash ahead of the reusable provider prefix.
_INTERIOR_USER_PAYLOAD_ORDER = (
    "configured_character_disposition",
    "lived_evidence_scope",
    "eight_facets",
    "selective_recall",
    "presentation",
    "inner_life_snapshot",
    "context_reading",
    "living_choice_scope",
    "reflection_scope",
    "wire_contract",
    "purpose_instruction",
    "purpose_contract",
    "background_context_profile",
    "current_authorship_authority",
    "citeable_sources",
    "capability_manifest",
    "inner_turn",
    "correction",
    "reference_dictionary",
)

_EXPRESSION_RECONSIDERATION_DISPOSITIONS = frozenset(
    {"continue", "cancel", "defer", "merge", "supersede", "new_beat"}
)

_MEMORY_HOST_BOUND_DECISION_GUIDANCE = (
    "For this memory review, decision contains only payload; do not supply source_refs. "
    "The host binds the original capability sources. Your choices and attended_source_refs remain yours."
)


def _canonical(value: object) -> str:
    return json.dumps(
        canonicalize_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pinned_attended_source_refs(
    attended: object, *, snapshot_refs: Sequence[str]
) -> list[str]:
    """Copy already-authored attention onto the decision envelope.

    DeepSeek's documented strict subset still lets the model flatten a
    complete purpose payload into ``decision``.  Closing that wrapper over
    pinned ``attended_source_refs`` does not invent evidence or a choice.
    """

    if not isinstance(attended, list):
        return []
    allowed = set(snapshot_refs)
    closed: list[str] = []
    seen: set[str] = set()
    for item in attended:
        if (
            not isinstance(item, str)
            or not item
            or item not in allowed
            or item in seen
        ):
            continue
        closed.append(item)
        seen.add(item)
    return closed


_MAX_CITEABLE_SOURCES_FOR_ATTENTION = 8


def _unique_refs_in_order(*groups: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for ref in group:
            if not isinstance(ref, str) or not ref or ref in seen:
                continue
            ordered.append(ref)
            seen.add(ref)
    return tuple(ordered)


def _bound_proactive_attended_source_refs(decoded: dict[str, object]) -> None:
    """Keep proactive attention provenance inside the wire cap.

    ``attended_source_refs`` records what she noticed, not the expression
    choice itself.  When a provider copies more snapshot refs than the eight
    allowed attention slots, keep the first eight unique refs in listed order
    so beats, summary, and decision payload stay intact.
    """

    raw = decoded.get("attended_source_refs")
    if not isinstance(raw, list):
        return
    bounded: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item or item in seen:
            continue
        bounded.append(item)
        seen.add(item)
        if len(bounded) >= _MAX_CITEABLE_SOURCES_FOR_ATTENTION:
            break
    decoded["attended_source_refs"] = bounded


def _bind_host_owned_source_lists(
    decoded: dict[str, object],
    *,
    request: "_InteriorRoleRequest",
    offered_attention: frozenset[str],
    max_attention: int,
) -> None:
    """Do the clerical work the host already knows, before strict validation.

    Production logs (2026-09..10) showed most first-draft rejections were
    bookkeeping, not meaning: attention lists one item too long, an attended
    ref that was not offered this turn, a host-bound decision whose
    ``source_refs`` were not a verbatim copy of the manifest, or the one-valued
    ``user_channel_completion`` flag.  None of those carry her choice:

    * ``attended_source_refs`` records what she noticed.  Refs that cannot be
      resolved to an offered source are dropped (never replaced), duplicates
      collapse, and her order is kept up to the wire cap.
    * For host-bound purposes the host owns ``decision.source_refs`` and writes
      the exact manifest binding; her ``payload`` is untouched.
    * ``activity_lifecycle_choice``: ``user_channel_completion`` has one legal
      value and follows ``noticed``; it is derived, not authored.

    Semantic fields, payload choices, statuses and proposals are never changed.
    """

    summary = decoded.get("summary")
    if isinstance(summary, list) and len(summary) == 1 and isinstance(summary[0], str):
        decoded["summary"] = summary[0]
    if request.purpose in _HOST_BOUND_DECISION_PURPOSES and "source_refs" in decoded:
        root_refs = decoded.pop("source_refs", None)
        if (
            not decoded.get("attended_source_refs")
            and isinstance(root_refs, list)
            and all(isinstance(ref, str) for ref in root_refs)
        ):
            decoded["attended_source_refs"] = root_refs
    attention = decoded.get("attended_source_refs")
    if isinstance(attention, list) and all(isinstance(ref, str) for ref in attention):
        kept = [ref for ref in _unique_refs_in_order(attention) if ref in offered_attention]
        decoded["attended_source_refs"] = kept[:max_attention]
    if request.purpose == "world_stimulus_appraisal" and isinstance(decoded.get("proposals"), list):
        repaired_proposals = []
        for prop in decoded["proposals"]:
            if isinstance(prop, dict):
                prop = dict(prop)
                if prop.get("reflection_depth") is None and any(
                    prop.get(field) is None
                    for field in ("behavior_tendency", "stance", "display_strategy")
                ):
                    prop["reflection_depth"] = "brief"
                if (
                    prop.get("decision") == "no_change"
                    and prop.get("meaning_candidates") is None
                    and prop.get("affect_transition") is None
                    and prop.get("relationship_signal") is None
                    and prop.get("severity") == 0
                ):
                    prop["severity"] = None
                    prop["attribution"] = None
            repaired_proposals.append(prop)
        decoded["proposals"] = repaired_proposals
    decision = decoded.get("decision")
    manifest = request.capability_manifest
    if (
        request.purpose == "private_impression_reflection"
        and manifest is not None
        and isinstance(decoded.get("proposals"), list)
    ):
        raw_sources = manifest.payload.get("reflection_sources")
        raw_token_map = manifest.payload.get("token_map")
        anchor_refs = manifest.payload.get("anchor_source_refs")
        if isinstance(raw_sources, list):
            offered_list = [
                item.get("source_ref")
                for item in raw_sources
                if isinstance(item, dict) and isinstance(item.get("source_ref"), str)
            ]
            offered_set = set(offered_list)
            existing_impressions = {
                item.get("source_ref")
                for item in raw_sources
                if isinstance(item, dict)
                and item.get("source_kind") == "existing_impression"
                and isinstance(item.get("source_ref"), str)
            }
            alias_to_refs: dict[str, list[str]] = {}
            if isinstance(raw_token_map, dict):
                for tok, sref in raw_token_map.items():
                    if isinstance(tok, str) and isinstance(sref, str) and sref in offered_set:
                        alias_to_refs.setdefault(tok, []).append(sref)
            for item in raw_sources:
                if not isinstance(item, dict):
                    continue
                sref = item.get("source_ref")
                if not isinstance(sref, str):
                    continue
                alias_to_refs.setdefault(sref, []).append(sref)
                if sref.startswith("private-impression:"):
                    alias_to_refs.setdefault(sref.removeprefix("private-impression:"), []).append(sref)
                auth_ref = item.get("authority_event_ref")
                if isinstance(auth_ref, str) and auth_ref:
                    alias_to_refs.setdefault(auth_ref, []).append(sref)
                val_json = item.get("value_json")
                if isinstance(val_json, str):
                    try:
                        val_obj = json.loads(val_json)
                    except json.JSONDecodeError:
                        val_obj = None
                    if isinstance(val_obj, dict):
                        for id_key in ("impression_id", "appraisal_id", "component_id", "episode_id"):
                            id_val = val_obj.get(id_key)
                            if isinstance(id_val, str) and id_val:
                                alias_to_refs.setdefault(id_val, []).append(sref)
            anchors = [
                item for item in anchor_refs if isinstance(item, str) and item in offered_set
            ] if isinstance(anchor_refs, list) else []
            repaired_pi_proposals = []
            for prop in decoded["proposals"]:
                if isinstance(prop, dict):
                    prop = dict(prop)
                    dec = prop.get("decision")
                    raw_preds = prop.get("predecessor_refs")
                    if dec == "retain":
                        prop["predecessor_refs"] = []
                    elif isinstance(raw_preds, list):
                        mapped_preds: list[str] = []
                        for item in raw_preds:
                            if isinstance(item, str):
                                for candidate in alias_to_refs.get(item, ()):
                                    if candidate in existing_impressions and candidate not in mapped_preds:
                                        mapped_preds.append(candidate)
                        if mapped_preds:
                            prop["predecessor_refs"] = mapped_preds
                    raw_srefs = prop.get("source_refs")
                    if isinstance(raw_srefs, list):
                        mapped_srefs: list[str] = []
                        for item in raw_srefs:
                            if isinstance(item, str):
                                for candidate in alias_to_refs.get(item, ()):
                                    if candidate in offered_set and candidate not in mapped_srefs:
                                        mapped_srefs.append(candidate)
                        for pred in prop.get("predecessor_refs", ()):
                            if isinstance(pred, str) and pred in offered_set and pred not in mapped_srefs:
                                mapped_srefs.append(pred)
                        if anchors and not (set(mapped_srefs) & set(anchors)):
                            mapped_srefs.insert(0, anchors[0])
                        if mapped_srefs:
                            prop["source_refs"] = mapped_srefs
                repaired_pi_proposals.append(prop)
            decoded["proposals"] = repaired_pi_proposals
    if not isinstance(decision, dict):
        return
    decision = dict(decision)
    if decoded.get("status") is None and isinstance(decision.get("status"), str):
        decoded["status"] = decision.pop("status")
    if "recall_query" in decision and decision.get("recall_query") is None:
        decoded.setdefault("recall_query", decision.pop("recall_query"))
    if "proposals" in decision and decision.get("proposals") == []:
        decoded.setdefault("proposals", decision.pop("proposals"))
    if decision.get("type") == "object":
        decision.pop("type", None)
    if decision.get("proposal_extra") == "ignored":
        decision.pop("proposal_extra", None)
    decoded["decision"] = decision
    if (
        manifest is not None
        and request.purpose in _HOST_BOUND_DECISION_PURPOSES
        and "payload" in decision
    ):
        decision = {**decision, "source_refs": list(manifest.source_refs)}
        decoded["decision"] = decision
    payload = decision.get("payload")
    if request.purpose == "activity_lifecycle_choice" and isinstance(payload, dict):
        noticed = payload.get("noticed")
        payload = dict(payload)
        if payload.get("decision") == "no_op" and payload.get("selected_token") is None:
            payload.pop("selected_token", None)
        if isinstance(noticed, str) and noticed.strip():
            payload["user_channel_completion"] = "none"
        elif noticed is None:
            payload.pop("user_channel_completion", None)
        decoded["decision"] = {**decision, "payload": payload}
    if (
        request.purpose == "world_stimulus_appraisal"
        and os.environ.get("WORLD_V2_LIFE_DEVELOPMENT_MIN_INTERVAL_SECONDS", "").strip()
    ):
        proposals = decoded.get("proposals")
        if isinstance(proposals, list) and len(proposals) == 1 and isinstance(proposals[0], dict):
            prop = dict(proposals[0])
            prop["proposal_type"] = "world_stimulus_appraisal_result"
            expected_status = (
                "transition"
                if prop.get("decision") == "activate"
                or prop.get("aspiration_transition") is not None
                or prop.get("experience_transition") is not None
                or prop.get("life_intent") is not None
                or prop.get("plan_response") is not None
                or prop.get("life_responses") is not None
                else "no_change"
            )
            decoded["status"] = expected_status
            decoded["proposals"] = [prop]
    if (
        request.purpose == "external_perception_attention"
        and manifest is not None
        and isinstance(payload, dict)
    ):
        allowed_decision_refs = set(request.snapshot.source_refs) | set(manifest.source_refs)
        allowed_attention_refs = set(request.attention_source_refs) | allowed_decision_refs
        raw_attended = decoded.get("attended_source_refs")
        if isinstance(raw_attended, list):
            filtered_attended = [
                ref
                for ref in raw_attended
                if isinstance(ref, str) and ref in allowed_attention_refs
            ]
            decoded["attended_source_refs"] = (
                filtered_attended[:8] if filtered_attended else list(manifest.source_refs[:8])
            )
        existing_refs = (
            decision.get("source_refs")
            if isinstance(decision.get("source_refs"), list)
            else []
        )
        merged_refs = list(
            dict.fromkeys(
                [
                    *manifest.source_refs,
                    *(
                        ref
                        for ref in existing_refs
                        if isinstance(ref, str) and ref in allowed_decision_refs
                    ),
                ]
            )
        )
        payload = dict(payload)
        allowed_ctx = manifest.payload.get("allowed_context_refs")
        if isinstance(allowed_ctx, list) and isinstance(payload.get("selections"), list):
            allowed_ctx_set = {
                ref for ref in allowed_ctx if isinstance(ref, str)
            } & set(request.snapshot.source_refs)
            normalized_selections: list[object] = []
            for item in payload["selections"]:
                if isinstance(item, dict) and isinstance(
                    item.get("attended_context_refs"), list
                ):
                    item = {
                        **item,
                        "attended_context_refs": [
                            ref
                            for ref in item["attended_context_refs"]
                            if isinstance(ref, str) and ref in allowed_ctx_set
                        ],
                    }
                normalized_selections.append(item)
            payload = {"selections": normalized_selections}
        decoded["decision"] = {
            **decision,
            "source_refs": merged_refs,
            "payload": payload,
        }


_VOLATILE_CITEABLE_SOURCE_PREFIXES = (
    "event:trigger:",
    "event:expression-plan:",
    "action:",
    "advisory:",
    "affect:",
    "appraisal:",
    "dialogue:",
    "event:dialogue:",
)


def _citeable_catalog_for_request(request: _InteriorRoleRequest) -> PinnedSourceCatalog:
    # The attention result has eight slots; that is not a limit on the
    # evidence she may read or cite. Keep the complete pinned catalog and
    # place stable background refs ahead of per-turn volatile refs so
    # lean aliases (s0..sN) inside stable snapshot slices do not renumber
    # every time a new dialogue beat, action, or appraisal is recorded.
    refs = request.attention_source_refs
    if request.snapshot.life_source_origin is None:
        stable = [r for r in refs if not r.startswith(_VOLATILE_CITEABLE_SOURCE_PREFIXES)]
        volatile = [r for r in refs if r.startswith(_VOLATILE_CITEABLE_SOURCE_PREFIXES)]
        refs = (*stable, *volatile)
    return PinnedSourceCatalog.from_refs(
        refs,
        labels=citeable_source_labels(request.snapshot),
    )


_HOST_BOUND_DECISION_PURPOSES = frozenset({
    "fact_memory_retention", "prehistory_memory_batch", "experience_memory_retention",
    "memory_withdrawal_review", "memory_consolidation", "activity_lifecycle_choice",
})


def _decision_source_refs_for_request(request):
    manifest = request.capability_manifest
    if manifest is not None and request.purpose in _HOST_BOUND_DECISION_PURPOSES:
        return set(manifest.source_refs)
    return set(request.snapshot.source_refs) | (set(manifest.source_refs) if manifest else set())


def _citeable_prompt_for_request(request: _InteriorRoleRequest) -> dict[str, object]:
    view = _citeable_catalog_for_request(request).prompt_value()
    if request.source_reference_contract is not None:
        eligible = _decision_source_refs_for_request(request)
        view["decision_source_ids"] = [item["id"] for item in view["items"] if item["ref"] in eligible]
        view["instruction"] = (
            "attended_source_refs 只写下面的 id 或原样 ref。decision.source_refs 只选 decision_source_ids 对应的 id/ref；"
            "注意到来源不等于获得效果权限。world_claims 还须遵守其范围权限。其他字段中的标识符不会增加来源权限。"
            "点名哪些、引不引以及如何选择仍由你决定。"
        )
    manifest = request.capability_manifest
    if request.purpose != "proactive_contact" or manifest is None:
        return view
    raw = manifest.payload.get("world_claim_source_lanes")
    if raw is None:
        return view
    from ..expression_draft import ProactiveWorldClaimSourceLanes

    lanes = ProactiveWorldClaimSourceLanes.model_validate_json(json.dumps(raw))
    for item in view["items"]:
        item["world_claim_scopes"] = [
            scope for scope, refs in sorted(lanes.source_refs_by_scope.items())
            if item["ref"] in refs
        ]
    view["instruction"] += (
        " world_claim_scopes 仅说明该来源允许用于哪些事实范围，不代表它支持你准备说的内容。"
        "空列表的来源可供理解和注意，但不能用来证明世界事实。"
        "最多注意8条不等于只能阅读或引用目录前8条；请自行选取与本次判断相关的来源。"
    )
    return view


def _rewrite_unique_source_refs(
    value: object,
    *,
    catalog: PinnedSourceCatalog,
) -> object:
    rewritten, _failures = resolve_pinned_source_ref_list(value, catalog)
    return rewritten if isinstance(value, list) else value


def _rewrite_claim_source_refs(
    claims: list[object],
    *,
    catalog: PinnedSourceCatalog,
) -> list[object]:
    restored: list[object] = []
    for claim in claims:
        if not isinstance(claim, dict) or "source_refs" not in claim:
            restored.append(claim)
            continue
        restored.append(
            {
                **claim,
                "source_refs": _rewrite_unique_source_refs(
                    claim.get("source_refs"),
                    catalog=catalog,
                ),
            }
        )
    return restored


def _restore_unique_pinned_source_refs(
    decoded: dict[str, object],
    *,
    catalog: PinnedSourceCatalog,
) -> None:
    """Restore uniquely identified opaque refs before structural validation.

    Short catalog ids, collapsed duplicate segments, a missing ``beat`` slot
    with a unique trailing index, and unique hash fragments are transport
    damage, not a semantic choice.  Ambiguous or empty hits stay as written
    and fail closed at the ordinary unpinned check.
    """

    decoded["attended_source_refs"] = _rewrite_unique_source_refs(
        decoded.get("attended_source_refs"),
        catalog=catalog,
    )
    raw_decision = decoded.get("decision")
    if not isinstance(raw_decision, dict):
        return
    updated = dict(raw_decision)
    if "source_refs" in updated:
        updated["source_refs"] = _rewrite_unique_source_refs(
            updated.get("source_refs"),
            catalog=catalog,
        )
    payload = updated.get("payload")
    if isinstance(payload, dict):
        payload = dict(payload)
        claims = payload.get("world_claims")
        if isinstance(claims, list):
            payload["world_claims"] = _rewrite_claim_source_refs(claims, catalog=catalog)
        media_refs = payload.get("media_source_refs")
        if isinstance(media_refs, list):
            payload["media_source_refs"] = _rewrite_unique_source_refs(
                media_refs,
                catalog=catalog,
            )
        selections = payload.get("selections")
        if isinstance(selections, list):
            restored_selections: list[object] = []
            for selection in selections:
                if isinstance(selection, dict) and isinstance(
                    selection.get("attended_context_refs"), list
                ):
                    selection = {
                        **selection,
                        "attended_context_refs": _rewrite_unique_source_refs(
                            selection.get("attended_context_refs"),
                            catalog=catalog,
                        ),
                    }
                restored_selections.append(selection)
            payload["selections"] = restored_selections
        updated["payload"] = payload
    if isinstance(updated.get("world_claims"), list):
        updated["world_claims"] = _rewrite_claim_source_refs(
            updated["world_claims"],
            catalog=catalog,
        )
    decoded["decision"] = updated


_PRIVATE_IMPRESSION_IDENTITY_KEYS = frozenset(
    {
        "source",
        "sources",
        "source_ref",
        "source_refs",
        "authority_event_ref",
        "token_map",
        "anchor_source_refs",
        "id",
        "ids",
        "hash",
        "hashes",
    }
)


def _redact_private_impression_material(value: object) -> object:
    """Keep reflection meaning while removing authority identities from prompts.

    The complete capsule remains pinned in the host manifest and is still used
    for token-to-ref materialization.  This projection is only the provider
    view: long refs and event IDs are not useful semantic choices and asking a
    model to echo them creates avoidable source-closure failures.
    """
    from .appraisal_model_view import sanitize_inner_thought_prose

    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for key, item in value.items():
            k_str = str(key)
            if k_str.lower() in _PRIVATE_IMPRESSION_IDENTITY_KEYS or k_str.lower().endswith(
                ("_ref", "_refs", "_id", "_ids")
            ):
                continue
            redacted = _redact_private_impression_material(item)
            if k_str in {"reflection_summary", "meaning", "summary"} and isinstance(redacted, str):
                redacted = sanitize_inner_thought_prose(redacted)
            out[k_str] = redacted
        return out
    if isinstance(value, list):
        return [_redact_private_impression_material(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_private_impression_material(item) for item in value)
    return value


def _private_impression_provider_payload(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Build the private-impression capability projection sent to the model."""

    projected = {
        key: value
        for key, value in payload.items()
        if key not in {"reflection_capsule", "token_map", "anchor_source_refs"}
    }
    raw_sources = payload.get("reflection_sources")
    if isinstance(raw_sources, list):
        sources: list[dict[str, object]] = []
        for raw_source in raw_sources:
            if not isinstance(raw_source, Mapping):
                continue
            short_token = raw_source.get("short_token")
            source_kind = raw_source.get("source_kind")
            if not isinstance(short_token, str) or not isinstance(source_kind, str):
                continue
            source_view: dict[str, object] = {
                "short_token": short_token,
                "source_kind": source_kind,
            }
            value_json = raw_source.get("value_json")
            if isinstance(value_json, str):
                try:
                    decoded_value = json.loads(value_json)
                except json.JSONDecodeError:
                    decoded_value = None
                if decoded_value is not None:
                    source_view["value"] = _redact_private_impression_material(
                        decoded_value
                    )
            sources.append(source_view)
        projected["reflection_sources"] = sources
        projected["reflection_capsule"] = {
            "source_count": len(sources),
            "anchor_short_tokens": list(
                payload.get("anchor_short_tokens", ())
                if isinstance(payload.get("anchor_short_tokens"), list)
                else ()
            ),
        }
    projected["source_identity_policy"] = (
        "Choose supplied short tokens. Full source and authority refs are host-bound "
        "and must not be echoed by the character."
    )
    return projected


def _memory_consolidation_provider_payload(payload, *, compact=False):
    """Remove host proof bytes, preserving source prose, ownership and status."""
    if compact:
        from ..memory_consolidation_contract import quote_handle_view
        payload = quote_handle_view(payload)
    proof_fields = {"authority_event_ref", "authority_world_revision", "authority_payload_hash",
                    "source_values_hash", "excerpt_ref", "excerpt_payload_hash", "source_bindings",
                    "consumed_source_authority_ids", "summary_payload_hash", "semantic_fingerprint",
                    "origin", "matrix_digest", "matrix_version"}
    def view(value):
        if isinstance(value, list):
            return [view(item) for item in value]
        if isinstance(value, dict):
            return {key:view(item) for key,item in value.items() if key not in proof_fields}
        return value
    result = view({key:value for key,value in payload.items() if key != "attention_token_map"})
    from .appraisal_model_view import sanitize_inner_thought_prose
    standing = result.get("standing_understandings")
    if isinstance(standing, list):
        for item in standing:
            if isinstance(item, dict) and isinstance(item.get("summary"), str):
                item["summary"] = sanitize_inner_thought_prose(item["summary"])
    if not compact:
        return result
    from .inbound_context_presentation import pack_flat_literal_rows

    for field in ("memories",):
        items = result.get(field)
        if isinstance(items, list):
            result[field] = pack_flat_literal_rows(items)
    result["source_row_presentation"] = (
        "literal-flat-row-table.1: columns are JSON-pointer field paths; stable_rows contain the exact cells "
        "in that order. absent_cell means missing, not null. Each row is one original object. "
        "Tokens are literal. quote_parts resolve through quote_options in order to the complete original prose."
    )
    result["understanding_instruction"] = (
        "你决定保留或释放一份有来源的理解，也可以选择[]。每项写disposition=retain或release，"
        "明确列出你选择替代的predecessor_tokens，保留summary、facet、confidence_bp。"
        "理解用于从你自己的真实生活处境（家里近况与经济压力、学业与勤工助学、社团与身边人关系、与对方的相处经历）提炼出这一阶段的行为习惯、自我要求与处事心气："
        "写清因为自己所处的具体生活处境，所以这一阶段做事养成了什么习惯、对自己有什么要求（例如因为家里变故或经济吃紧，所以最近做事都要注意什么、要自立不给家里添麻烦）。"
        "单次事件的具体金额、具体日期或编号已保存在底层记忆并由evidence_quotes绑定；修订旧的standing_understandings时直接整句重写为当前阶段的完整理解，不在旧句末尾机械续写，也不必在长期理解里背诵单次事件数字。summary使用纯中文自然第一人称大白话，不夹杂英文单词。"
        "private_self写因家庭、经济或学业处境养成的阶段生活习惯、自我要求与日常步调；"
        "subjective_relationship与expression_stance写从与身边人或对方往来里长出来的相处原则、分寸与说话心气；"
        "aspirations_conflicts与emotional_continuity写这一阶段因现实处境在心里压着的重担、心结或憋着的一股劲。"
        "evidence_quotes每项只写quote_ref，选择quote_options里的原文编号，不重抄或改写引文；"
        "quote_parts按顺序拼回完整原文，不是删减过的摘要。"
        "retain配空前身表示新理解，配一个前身表示修订，配多个前身表示合并；release必须选一个前身。"
        "不要再写operation。前身只能选understanding_tokens里的现存条目，引用只能选实际提供的来源。"
        "不能同时忘掉来源又用它产生新理解。"
        "evidence_quotes只用understanding_source_tokens中的m/k条目，不能引用一般注意目录的s条目。"
        "forget表示完全遗忘这个候选及其依赖的新理解；若仍想保留它的影响，需要自行选择retain该来源。"
    )
    return result


class StructuredRoleResultError(_RoleResultContractError):
    """A model-authored result that violates the structural role contract."""

    def __init__(
        self,
        code: str,
        *,
        detail: str,
        response_hash: str | None = None,
        rejected_raw: str | None = None,
        request_hash: str | None = None,
        model_call_id: str | None = None,
    ) -> None:
        super().__init__(
            code,
            detail=detail,
            response_hash=response_hash,
            rejected_raw=rejected_raw,
            request_hash=request_hash,
            model_call_id=model_call_id,
        )


PayloadValidator = Callable[[Mapping[str, object], frozenset[str]], None]


@dataclass(frozen=True, slots=True)
class PurposeDecisionContract:
    """Extensible structural contract for one authorized purpose.

    ``validator`` may check only syntax and the offered capability set.  The
    role model remains the sole owner of the semantic choice.
    """

    purpose: str
    payload_contract: str
    capability_kind: str | None = None
    offered_token_fields: tuple[str, ...] = ()
    selected_token_required: bool = False
    decision_required: bool = False
    proposals_allowed: bool = True
    proposal_type: str | None = None
    proposal_payload_contract: str | None = None
    validator: PayloadValidator | None = None

    def __post_init__(self) -> None:
        if not self.purpose or not self.payload_contract:
            raise ValueError("purpose decision contract identity is incomplete")
        if self.selected_token_required and self.capability_kind is None:
            raise ValueError("selected-token contract needs a capability kind")
        if (self.proposal_type is None) != (self.proposal_payload_contract is None):
            raise ValueError("typed proposal contract identity is incomplete")


def _is_explicit_media_no_op(payload: Mapping[str, object]) -> bool:
    """``no_op`` may omit the token or send JSON null.  Both mean decline."""

    if payload.get("decision") != "no_op":
        return False
    extra = set(payload) - {"decision", "selected_token"}
    if extra:
        return False
    return payload.get("selected_token") is None


def _validate_media_selection_payload(
    payload: Mapping[str, object],
    offered_tokens: frozenset[str],
) -> None:
    """Close only the media wire shape, never the character's preference."""

    decision = payload.get("decision")
    if _is_explicit_media_no_op(payload):
        return
    selected_token = payload.get("selected_token")
    if decision == "select":
        if not isinstance(selected_token, str) or not selected_token:
            raise StructuredRoleResultError(
                "selected_token_required",
                detail=_FAILURE_DETAILS["selected_token_required"],
            )
        if selected_token not in offered_tokens:
            raise StructuredRoleResultError(
                "selected_token_not_offered",
                detail=_FAILURE_DETAILS["selected_token_not_offered"],
            )
        if set(payload) == {"decision", "selected_token"}:
            return
    raise ValueError("media selection must be no_op or select exactly one offered token")


def _validate_reconsideration_payload(
    payload: Mapping[str, object],
    offered_tokens: frozenset[str],
) -> None:
    if set(payload) != {"disposition"} or payload.get("disposition") not in offered_tokens:
        raise ValueError("expression reconsideration disposition is outside the offered capability")
    if payload.get("disposition") not in _EXPRESSION_RECONSIDERATION_DISPOSITIONS:
        raise ValueError("expression reconsideration needs one supported disposition")


class _ExpressionReconsiderationPayload(BaseModel):
    """Canonical role-authored disposition for an interrupted expression."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    disposition: Literal[
        "continue",
        "cancel",
        "defer",
        "merge",
        "supersede",
        "new_beat",
    ]


_MemoryBasisPointInteger = Annotated[int, Field(ge=0, le=10_000)]
_MemoryBasisPointNumber = Annotated[float, Field(ge=0, le=10_000)]


class _MemorySaliencePayload(BaseModel):
    """Provider-facing salience values without reducer-owned matrix metadata."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    autobiographical_relevance_bp: _MemoryBasisPointInteger | _MemoryBasisPointNumber
    relationship_relevance_bp: _MemoryBasisPointInteger | _MemoryBasisPointNumber
    emotional_residue_bp: _MemoryBasisPointInteger | _MemoryBasisPointNumber
    unfinished_business_bp: _MemoryBasisPointInteger | _MemoryBasisPointNumber
    recurrence_bp: _MemoryBasisPointInteger | _MemoryBasisPointNumber
    novelty_bp: _MemoryBasisPointInteger | _MemoryBasisPointNumber
    future_utility_bp: _MemoryBasisPointInteger | _MemoryBasisPointNumber
    world_continuity_bp: _MemoryBasisPointInteger | _MemoryBasisPointNumber


class _MemoryRetentionNoChangePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    retain: Literal[False]


class _MemoryRetentionPayloadValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    retain: Literal[True]
    cue_kind: MemoryCueKind
    retention_rationales: list[MemoryRetentionRationale] = Field(
        min_length=1,
        max_length=8,
    )
    salience: _MemorySaliencePayload

    @model_validator(mode="after")
    def rationales_are_unique(self) -> "_MemoryRetentionPayloadValue":
        if len(self.retention_rationales) != len(set(self.retention_rationales)):
            raise ValueError("memory retention rationales must be unique")
        return self


class _MemoryRetentionPayload(
    RootModel[_MemoryRetentionNoChangePayload | _MemoryRetentionPayloadValue]
):
    """Canonical retain/no-change wire shared by fact and experience memory."""


class _MemoryWithdrawalReviewPayload(BaseModel):
    """Canonical disposition wire for one withdrawn-memory review."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    selected_token: str = Field(min_length=1, max_length=32)


_PROACTIVE_SOURCE_LANES_ABSENT = object()


def _validate_proactive_payload(
    payload: Mapping[str, object],
    _offered_tokens: frozenset[str],
    *, source_lanes: object = _PROACTIVE_SOURCE_LANES_ABSENT,
) -> None:
    # Import locally so the deep Module's generic role contract does not make
    # proactive scheduling a dependency of every CharacterInterior import.
    from ..expression_draft import (
        ProactiveWorldClaimSourceLanes,
        bind_proactive_expression_wire,
        normalize_expression_draft_wire,
    )
    from ..proactive_action import ProactiveDraft
    from ..private_turn_state import validate_authored_impression_retention

    appraisal = payload.get("appraisal_draft")
    validate_authored_impression_retention(
        payload, appraisal=appraisal if isinstance(appraisal, dict) else None,
    )
    if "private_turn_state" in payload:
        raise ValueError("proactive private_turn_state is supplied by the same InnerTurn summary")
    normalized = bind_proactive_expression_wire(
        normalize_expression_draft_wire(
            {
                **dict(payload),
                "private_turn_state": {
                    "inner_state_summary": "structural validation only",
                    "attended_source_refs": [],
                },
            }
        )
    )
    draft = ProactiveDraft.model_validate_json(_canonical(normalized), strict=True)
    if draft.appraisal_draft is not None:
        from .inbound_appraisal_wire import canonicalize_appraisal_draft_wire

        canonicalize_appraisal_draft_wire(draft.appraisal_draft)
    if source_lanes is not _PROACTIVE_SOURCE_LANES_ABSENT:
        lanes = ProactiveWorldClaimSourceLanes.model_validate_json(_canonical(source_lanes), strict=True)
        lanes.validate_claims(draft.world_claims)


def _validate_life_choice_payload(
    payload: Mapping[str, object],
    _offered_tokens: frozenset[str],
) -> None:
    if set(payload) != {"completion"} or not isinstance(payload.get("completion"), dict):
        raise ValueError("life choice needs exactly one complete JSON completion object")
    if "recall_request" in payload["completion"]:
        raise ValueError("nested Life recall is unavailable; use the outer recall_request status")


def _normalized_life_development_completion(
    payload: Mapping[str, object],
    *,
    manifest: _InteriorCapabilityManifest,
    response_hash: str | None = None,
) -> dict[str, object]:
    """Close one Life choice over the exact trusted capability envelope.

    The business runtime must not discover a domain error after the InnerTurn
    has completed and then manufacture a second opportunity.  Keep the full
    shape and cross-field authority check beside the structured role so the
    ordinary CharacterInterior correction lifecycle owns the only reselection.
    """

    from ..life_development_draft import (
        LifeDevelopmentDraftError,
        LifeDevelopmentPossibilityDraft,
        parse_character_choice,
    )
    from ..schemas import DueWindow

    completion = payload.get("completion")
    capability = manifest.payload
    external_opportunity = capability.get("external_opportunity")
    executable_envelope = capability.get("executable_envelope")
    active_aspirations = capability.get("active_aspiration_source_refs", [])
    try:
        if not isinstance(completion, dict):
            raise ValueError("life choice completion must be one JSON object")
        if not isinstance(external_opportunity, dict):
            raise ValueError("life choice capability lacks its external opportunity")
        if not isinstance(executable_envelope, dict):
            raise ValueError("life choice capability lacks its executable envelope")
        if not isinstance(active_aspirations, list) or any(
            not isinstance(item, str) or not item for item in active_aspirations
        ):
            raise ValueError("life choice aspiration authority is malformed")
        offered = LifeDevelopmentPossibilityDraft.model_validate_json(
            _canonical(external_opportunity),
        )
        offered_window = DueWindow.model_validate_json(
            _canonical(
                {
                    "opens_at": executable_envelope.get("opens_at"),
                    "closes_at": executable_envelope.get("closes_at"),
                }
            )
        )
        participant_refs = executable_envelope.get("participant_refs")
        if participant_refs != list(offered.entity_refs):
            raise ValueError("life choice executable participants changed the offered opportunity")
        if completion.get("plan_response") is not None:
            from ..chat_life_intent_contract import PlanResponseDraft
            from ..world_plan_management import validate_choice
            validate_choice(PlanResponseDraft.model_validate_json(_canonical(completion["plan_response"])),
                            capability.get("plan_management"))
        parsed = parse_character_choice(
            raw=_canonical(completion),
            offered=offered,
            offered_window=offered_window,
            active_aspiration_source_refs=tuple(active_aspirations),
        )
    except LifeDevelopmentDraftError as exc:
        raise StructuredRoleResultError(
            exc.code,
            detail=exc.detail,
            response_hash=response_hash,
        ) from exc
    except (TypeError, ValueError) as exc:
        raise StructuredRoleResultError(
            "role_result_schema_invalid",
            detail=str(exc),
            response_hash=response_hash,
        ) from exc
    return parsed.model_dump(mode="json")


def _validate_activity_lifecycle_payload(
    payload: Mapping[str, object],
    offered_tokens: frozenset[str],
) -> None:
    noticed = payload.get("noticed")
    if noticed is not None and (not isinstance(noticed, str) or not noticed.strip()):
        raise ValueError("activity noticed must be non-empty text")
    completion = payload.get("user_channel_completion")
    if noticed is None and completion is not None:
        raise ValueError("activity user_channel_completion requires noticed")
    if noticed is not None and completion != "none":
        raise ValueError('activity noticed requires user_channel_completion="none" (the literal JSON string, not null or omission)')
    keys = {key for key in payload if key not in {"noticed", "user_channel_completion"}}
    decision = payload.get("decision")
    if decision == "no_op":
        if keys != {"decision"}:
            raise ValueError("activity lifecycle no_op may contain only decision")
        return
    if decision != "select" or keys != {"decision", "selected_token"}:
        raise ValueError("activity lifecycle must select one offered token or no_op")
    selected = payload.get("selected_token")
    if not isinstance(selected, str) or selected not in offered_tokens:
        raise ValueError("activity lifecycle selected an unavailable token")


def _validate_day_open_payload(payload, offered_tokens) -> None:
    del offered_tokens
    if payload == {"decision": "no_op"}:
        return
    if set(payload) != {"decision", "life_intent"} or payload.get("decision") != "self_directed_intent":
        raise ValueError("day_open requires one explicit self_directed_intent or no_op")
    LifeIntentDraft.model_validate(payload["life_intent"])


def _validate_day_open_reconsider_payload(payload, offered_tokens) -> None:
    if payload.get("decision") == "reconsider":
        from ..day_open_life_intent_contract import DayOpenReconsiderChoice

        DayOpenReconsiderChoice.model_validate(payload)
    else:
        _validate_day_open_payload(payload, offered_tokens)



def _validate_day_open_agenda_payload(payload, offered_tokens) -> None:
    if payload.get("decision") in {"no_op", "reconsider"}:
        _validate_day_open_reconsider_payload(payload, offered_tokens)
        return
    from ..day_open_life_intent_contract import DayOpenAgendaChoice

    DayOpenAgendaChoice.model_validate_json(json.dumps(payload))


def _validate_day_open_continuity_payload(payload, offered_tokens, plans) -> None:
    if payload.get("decision") == "continue_existing":
        from ..day_open_life_intent_contract import validate_continuation_choice
        validate_continuation_choice(payload, plans)
    else:
        _validate_day_open_agenda_payload(payload, offered_tokens)


def _validate_day_open_management_payload(payload, offered_tokens, management):
    if payload.get("decision") == "continue_existing":
        from ..day_open_life_intent_contract import validate_continuation_choice
        validate_continuation_choice(payload,management["plans"])
        return
    if payload.get("decision") == "manage_plan":
        from ..day_open_life_intent_contract import DayOpenPlanChoice
        from ..world_plan_management import validate_choice
        choice=DayOpenPlanChoice.model_validate_json(json.dumps(payload))
        validate_choice(choice.plan_response,management)
    else:
        _validate_day_open_agenda_payload(payload,offered_tokens)


def _validate_outcome_selection_payload(
    payload: Mapping[str, object],
    offered_tokens: frozenset[str],
) -> None:
    """Close one consequential choice over the exact observed alternatives."""

    if set(payload) != {
        "selected_token",
        "adopt_proposed_life_direction",
        "character_life_direction",
    }:
        raise ValueError(
            "outcome selection needs one selected token, an explicit adoption "
            "choice, and an optional character direction"
        )
    selected = payload.get("selected_token")
    if not isinstance(selected, str) or not selected:
        raise StructuredRoleResultError(
            "selected_token_required",
            detail=_FAILURE_DETAILS["selected_token_required"],
        )
    if selected not in offered_tokens:
        raise StructuredRoleResultError(
            "selected_token_not_offered",
            detail=_FAILURE_DETAILS["selected_token_not_offered"],
        )
    if not isinstance(payload.get("adopt_proposed_life_direction"), bool):
        raise ValueError("outcome selection needs an explicit adoption choice")
    direction = payload.get("character_life_direction")
    if direction is None:
        return
    from ..character_outcome_contract import CharacterLifeDirectionDraft
    from ..schemas import BiographicalCoordinateReplacement

    parsed = CharacterLifeDirectionDraft.model_validate(direction, strict=True)
    BiographicalCoordinateReplacement.create(
        coordinate_ref=parsed.coordinate_ref,
        summary=parsed.summary,
        context_tags=parsed.context_tags,
        replaces_context_tag_prefixes=parsed.replaces_context_tag_prefixes,
        privacy_class=parsed.privacy_class,
    )


class _OutcomeSelectionPayload(BaseModel):
    """Canonical role-authored outcome choice payload.

    The offered candidate set and the optional direction capability are
    specialized by the transport compiler.  This model only defines the
    complete semantic wire; it does not select a candidate or a direction.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    selected_token: str = Field(min_length=1, max_length=512)
    adopt_proposed_life_direction: bool
    character_life_direction: CharacterLifeDirectionDraft | None = None


class _ActivityLifecyclePayload(BaseModel):
    """Canonical role-authored activity opening choice payload."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    decision: Literal["no_op", "select"]
    selected_token: str | None = None
    noticed: str | None = Field(default=None, min_length=1, max_length=720)
    user_channel_completion: Literal["none"] | None = None

    @model_validator(mode="after")
    def choice_shape_is_closed(self) -> "_ActivityLifecyclePayload":
        if self.decision == "no_op" and self.selected_token is not None:
            raise ValueError("activity no_op cannot carry a selected token")
        if self.decision == "select" and not self.selected_token:
            raise ValueError("activity select requires a selected token")
        if self.noticed is None and self.user_channel_completion is not None:
            raise ValueError("activity user_channel_completion requires noticed")
        if self.noticed is not None and self.user_channel_completion != "none":
            raise ValueError('activity noticed requires user_channel_completion="none" (the literal JSON string, not null or omission)')
        return self


class _DayOpenPayload(BaseModel):
    """The same activity role may author one future private intention."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    decision: Literal["no_op", "self_directed_intent"]
    life_intent: LifeIntentDraft | None = None


class _MediaSelectionPayload(BaseModel):
    """Canonical role-authored media select/no-op payload.

    Candidate tokens are specialized from the pinned media capability by the
    transport compiler.  The model still owns whether to select a candidate
    or explicitly decline the opportunity.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    decision: Literal["no_op", "select"]
    selected_token: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def choice_shape_is_closed(self) -> "_MediaSelectionPayload":
        if self.decision == "no_op" and self.selected_token is not None:
            raise ValueError("media no_op cannot carry a selected token")
        if self.decision == "select" and not self.selected_token:
            raise ValueError("media select requires a selected token")
        return self


class _QQAttachmentPerceptionPayload(BaseModel):
    """Canonical role-authored attachment select/no-op payload."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    decision: Literal["no_op", "select"]
    selected_token: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def choice_shape_is_closed(self) -> "_QQAttachmentPerceptionPayload":
        if self.decision == "no_op" and self.selected_token is not None:
            raise ValueError("attachment perception no_op cannot carry a selected token")
        if self.decision == "select" and not self.selected_token:
            raise ValueError("attachment perception select requires a selected token")
        return self


def _validate_qq_attachment_perception_payload(
    payload: Mapping[str, object],
    offered_tokens: frozenset[str],
) -> None:
    decision = payload.get("decision")
    if decision == "no_op" and set(payload) == {"decision"}:
        return
    selected_token = payload.get("selected_token")
    if decision == "select":
        if not isinstance(selected_token, str) or not selected_token:
            raise StructuredRoleResultError(
                "selected_token_required",
                detail=_FAILURE_DETAILS["selected_token_required"],
            )
        if selected_token not in offered_tokens:
            raise StructuredRoleResultError(
                "selected_token_not_offered",
                detail=_FAILURE_DETAILS["selected_token_not_offered"],
            )
        if set(payload) == {"decision", "selected_token"}:
            return
    raise ValueError("attachment perception must be no_op or select exactly one offered token")


def _validate_prehistory_batch_payload(payload, offered_tokens):
    from ..prehistory_memory_batch import validate_batch_payload
    validate_batch_payload(payload, offered_tokens)


def _validate_memory_retention_payload(
    payload: Mapping[str, object],
    _offered_tokens: frozenset[str],
) -> None:
    # This is the installed Memory wire/matrix boundary, not a preference for
    # retention or forgetting.  Both choices remain authored by the role.
    from ..fact_memory_draft import materialize_fact_memory_draft

    materialize_fact_memory_draft(_canonical(dict(payload)))


class _PrivateImpressionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    proposal_type: Literal["private_impression_transition"]
    decision: Literal["retain", "consolidate", "supersede", "release"]
    predecessor_refs: list[str] = Field(default_factory=list, max_length=32)
    source_refs: list[str] = Field(min_length=1, max_length=48)
    reflection_summary: str = Field(min_length=1, max_length=1_200)
    confidence_bp: int = Field(ge=0, le=10_000)
    expiry_condition: Literal[
        "until_appraisal_contradicted",
        "until_counter_evidence",
        "until_relationship_stage_changes",
        "one_month_without_support",
    ]

    @model_validator(mode="after")
    def transition_shape_is_closed(self) -> "_PrivateImpressionProposal":
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("private impression source refs must be unique")
        if len(self.predecessor_refs) != len(set(self.predecessor_refs)):
            raise ValueError("private impression predecessor refs must be unique")
        if self.decision == "retain" and self.predecessor_refs:
            raise ValueError("retain cannot retire an existing impression")
        if self.decision != "retain" and not self.predecessor_refs:
            raise ValueError("consolidate/supersede/release require predecessor refs")
        if self.decision == "release" and len(self.predecessor_refs) != 1:
            raise ValueError("release puts down exactly one existing impression")
        if set(self.predecessor_refs) - set(self.source_refs):
            raise ValueError("private impression predecessors must also be selected sources")
        return self


class _WorldStimulusMeaningCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    meaning: str = Field(min_length=1, max_length=128)
    confidence: int = Field(ge=0, le=10_000)

    @field_validator("meaning")
    @classmethod
    def meaning_is_canonical_free_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("appraisal meaning cannot have surrounding whitespace")
        return value


class _WorldStimulusRelationshipDeltas(BaseModel):
    """A bounded suggestion, never direct relationship mutation authority."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    trust_bp: int = Field(ge=-10_000, le=10_000)
    closeness_bp: int = Field(ge=-10_000, le=10_000)
    respect_bp: int = Field(ge=-10_000, le=10_000)
    reliability_bp: int = Field(ge=-10_000, le=10_000)
    mutuality_bp: int = Field(ge=-10_000, le=10_000)
    repair_confidence_bp: int = Field(ge=-10_000, le=10_000)


class _WorldStimulusRelationshipSignal(BaseModel):
    """Optional relationship reading authored in the same private turn."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    subject_ref: str = Field(min_length=1, max_length=512)
    signal_code: str = Field(min_length=1, max_length=128)
    confidence_bp: int = Field(ge=1, le=10_000)
    persistence: Literal["session", "durable"]
    rationale_code: str = Field(min_length=1, max_length=128)
    suggested_deltas: _WorldStimulusRelationshipDeltas


class _WorldStimulusLifeIntentDraft(LifeIntentDraft):
    """A chosen future self-directed activity, grounded in one offered settlement."""

    source_event_ref: str = Field(min_length=1, max_length=512)


class _WorldStimulusLifeResponseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    source_event_ref: str = Field(min_length=1, max_length=512)
    response_text: str | None = Field(max_length=4000)


class _WorldStimulusAppraisalResult(BaseModel):
    """Wire closure only; the role still owns whether and how it appraises."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    proposal_type: Literal["world_stimulus_appraisal_result"]
    decision: Literal["no_change", "activate"]
    brief_rationale: str = Field(min_length=1, max_length=240)
    behavior_tendency: str | None = Field(min_length=1, max_length=128)
    stance: str | None = Field(min_length=1, max_length=128)
    display_strategy: str | None = Field(min_length=1, max_length=128)
    reflection_depth: Literal["brief", "elaborated"] | None = Field(
        default=None, exclude_if=lambda value: value is None)
    confidence: int = Field(ge=0, le=10_000)
    meaning_candidates: list[_WorldStimulusMeaningCandidate] | None = Field(
        default=None,
        min_length=1,
        max_length=16,
    )
    attribution: (
        Literal["user", "companion", "npc", "situation", "third_party", "unknown"] | None
    ) = None
    severity: int | None = Field(default=None, ge=0, le=10_000)
    expiry: datetime | None = None

    @model_validator(mode="after")
    def brief_owns_absent_explanations(self):
        if self.reflection_depth != "brief" and any(
            value is None for value in (self.behavior_tendency, self.stance, self.display_strategy)
        ):
            raise ValueError("only an explicitly brief reaction can omit explanations")
        return self

    @model_validator(mode="before")
    @classmethod
    def canonicalize_rationale(cls, value: object) -> object:
        # The role model frequently names the private reason "rationale"
        # instead of the wire's "brief_rationale".  Accept that exact alias.
        if isinstance(value, dict) and "rationale" in value and "brief_rationale" not in value:
            value = dict(value)
            value["brief_rationale"] = value.pop("rationale")
        # The model sometimes omits the semantic decision while still
        # carrying appraisal fields; derive it deterministically from the
        # carried content instead of failing the whole turn.
        if (
            isinstance(value, dict)
            and "decision" not in value
            and any(
                value.get(field) is not None
                for field in (
                    "meaning_candidates",
                    "affect_transition",
                    "relationship_signal",
                    "aspiration_transition",
                    "experience_transition",
                )
            )
        ):
            value = dict(value)
            value["decision"] = "activate"
        # The model sometimes echoes its attended sources into the proposal;
        # that binding already lives on the outer envelope, so drop the echo.
        if isinstance(value, dict) and "source_refs" in value:
            value = dict(value)
            value.pop("source_refs", None)
        return value

    @field_validator("expiry", mode="before")
    @classmethod
    def parse_iso_expiry(cls, value: object) -> object:
        # The role model naturally emits ISO-8601 strings; the wire demands a
        # timezone-aware datetime object.  Parse that exact representation so
        # an otherwise valid appraisal never fails solely on serialization.
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        return value

    affect_transition: InteriorAffectTransition | None = None
    relationship_signal: _WorldStimulusRelationshipSignal | None = None
    aspiration_transition: AspirationTransitionPayload | None = None
    experience_transition: ExperienceTransitionDraft | None = None
    life_intent: _WorldStimulusLifeIntentDraft | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    plan_response: WorldPlanResponseDraft | None = Field(default=None, exclude_if=lambda value: value is None)

    life_responses: tuple[_WorldStimulusLifeResponseDraft, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("life_responses", mode="before")
    @classmethod
    def parse_response_array(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def decision_closes_appraisal_shape(self) -> "_WorldStimulusAppraisalResult":
        if self.plan_response is not None and self.life_intent is not None:
            raise ValueError('choose one existing-Plan response or a new life_intent, not both')
        appraisal_fields = (
            self.meaning_candidates,
            self.attribution,
            self.severity,
        )
        if self.decision == "activate":
            if any(item is None for item in appraisal_fields):
                raise ValueError("activate requires one complete appraisal")
            assert self.meaning_candidates is not None
            meanings = [item.meaning for item in self.meaning_candidates]
            if len(meanings) != len(set(meanings)):
                raise ValueError("appraisal meanings must be unique")
            if sum(item.confidence for item in self.meaning_candidates) <= 0:
                raise ValueError("appraisal meaning weights cannot all be zero")
        elif (
            any(item is not None for item in appraisal_fields)
            or self.expiry is not None
            or self.affect_transition is not None
            or self.relationship_signal is not None
        ):
            raise ValueError("no_change cannot smuggle an appraisal")
        return self


_BUILTIN_CONTRACTS = (
    PurposeDecisionContract(
        purpose="media_selection",
        payload_contract="character-interior-media-selection-decision.1",
        capability_kind="media_selection",
        offered_token_fields=("offered_tokens", "candidates"),
        selected_token_required=False,
        proposals_allowed=False,
        validator=_validate_media_selection_payload,
    ),
    PurposeDecisionContract(
        purpose="external_perception_attention",
        payload_contract="external-perception-attention-decision.1",
        capability_kind="external_perception_attention",
        # External attention is deliberately zero-or-many.  The specialized
        # validator below closes every chosen candidate/revision/channel over
        # the exact source-bound window without turning this into a single
        # token behavior choice.
        selected_token_required=False,
        proposals_allowed=False,
    ),
    PurposeDecisionContract(
        purpose="qq_attachment_perception",
        payload_contract="character-interior-qq-attachment-perception-decision.1",
        capability_kind="qq_attachment_perception",
        # ``offered_tokens`` is the canonical transport vocabulary.  The
        # attachment descriptors are evidence, not a second token source;
        # keeping them out of this union makes the host validator agree with
        # the required-tool enum and fail closed on malformed providers.
        offered_token_fields=("offered_tokens",),
        decision_required=True,
        proposals_allowed=False,
        validator=_validate_qq_attachment_perception_payload,
    ),
    PurposeDecisionContract(
        purpose="proactive_contact",
        payload_contract="character-interior-proactive-contact-decision.1",
        capability_kind="proactive_contact",
        proposals_allowed=False,
        validator=_validate_proactive_payload,
    ),
    PurposeDecisionContract(
        purpose="expression_reconsideration",
        payload_contract="character-interior-expression-reconsideration-decision.1",
        capability_kind="expression_reconsideration",
        offered_token_fields=("allowed_dispositions",),
        proposals_allowed=False,
        validator=_validate_reconsideration_payload,
    ),
    PurposeDecisionContract(
        purpose="private_impression_reflection",
        payload_contract="character-interior-private-impression-decision.1",
        capability_kind="private_impression_reflection",
        offered_token_fields=("offered_tokens", "reflection_sources"),
        proposal_type="private_impression_transition",
        proposal_payload_contract="character-interior-private-impression-transition.1",
    ),
    PurposeDecisionContract(
        purpose="world_stimulus_appraisal",
        payload_contract="character-interior-world-stimulus-appraisal-decision.1",
        capability_kind="world_stimulus_appraisal",
        proposal_type="world_stimulus_appraisal_result",
        proposal_payload_contract="character-interior-world-stimulus-appraisal-result.1",
    ),
    PurposeDecisionContract(
        purpose="life_development_choice",
        payload_contract="character-interior-life-development-choice.2",
        capability_kind="life_development_choice",
        decision_required=True,
        proposals_allowed=False,
        validator=_validate_life_choice_payload,
    ),
    PurposeDecisionContract(
        purpose="activity_lifecycle_choice",
        payload_contract="character-interior-activity-lifecycle-choice.1",
        capability_kind="activity_lifecycle_choice",
        offered_token_fields=("offered_tokens",),
        decision_required=True,
        proposals_allowed=False,
        validator=_validate_activity_lifecycle_payload,
    ),
    PurposeDecisionContract(
        purpose="outcome_selection",
        payload_contract="character-interior-outcome-selection-decision.1",
        capability_kind="outcome_selection",
        offered_token_fields=("offered_tokens", "candidates"),
        selected_token_required=True,
        decision_required=True,
        proposals_allowed=False,
        validator=_validate_outcome_selection_payload,
    ),
    PurposeDecisionContract(
        purpose="prehistory_memory_batch", payload_contract="character-interior-prehistory-memory-batch.1",
        capability_kind="prehistory_memory_batch", offered_token_fields=("source_ids",),
        decision_required=True, proposals_allowed=False, validator=_validate_prehistory_batch_payload,
    ),
    PurposeDecisionContract(
        purpose="fact_memory_retention",
        payload_contract="character-interior-fact-memory-retention.1",
        capability_kind="fact_memory_retention",
        decision_required=True,
        proposals_allowed=False,
        validator=_validate_memory_retention_payload,
    ),
    PurposeDecisionContract(
        purpose="experience_memory_retention",
        payload_contract="character-interior-experience-memory-retention.1",
        capability_kind="experience_memory_retention",
        decision_required=True,
        proposals_allowed=False,
        validator=_validate_memory_retention_payload,
    ),
    PurposeDecisionContract(
        purpose="memory_consolidation",
        payload_contract="character-interior-memory-consolidation.1",
        capability_kind="memory_consolidation",
        offered_token_fields=("offered_tokens",),
        decision_required=True, proposals_allowed=False, validator=_validate_periodic_memory,
    ),
    PurposeDecisionContract(
        purpose="memory_withdrawal_review",
        payload_contract="character-interior-memory-withdrawal-review.1",
        capability_kind="memory_withdrawal_review",
        offered_token_fields=("offered_tokens",),
        selected_token_required=True,
        decision_required=True,
        proposals_allowed=False,
    ),
)


class _WireDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_refs: list[str] = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def refs_are_unique(self) -> "_WireDecision":
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("decision source refs must be unique")
        if any(not item for item in self.source_refs):
            raise ValueError("decision source refs cannot be empty")
        return self


class _WireRoleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: Literal["transition", "no_change", "decision", "silent", "recall_request"]
    summary: str = Field(min_length=1, max_length=1_024)
    attended_source_refs: list[str] = Field(default_factory=list, max_length=8)
    decision: _WireDecision | None = None
    recall_query: str | None = Field(default=None, min_length=1, max_length=1_024)
    proposals: list[dict[str, Any]] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def terminal_shape_matches_status(self) -> "_WireRoleResult":
        if not self.summary.strip():
            raise ValueError("role result summary cannot be blank")
        if len(self.attended_source_refs) != len(set(self.attended_source_refs)):
            raise ValueError("role attention refs must be unique")
        if self.status == "decision" and self.decision is None:
            raise ValueError("decision status requires decision")
        if self.status != "decision" and self.decision is not None:
            raise ValueError("only decision status may contain decision")
        if self.status == "recall_request" and self.recall_query is None:
            raise ValueError("recall_request requires recall_query")
        if self.status != "recall_request" and self.recall_query is not None:
            raise ValueError("only recall_request may contain recall_query")
        if self.status == "recall_request" and self.proposals:
            raise ValueError("recall_request cannot contain proposals")
        return self


class _ExpandedAttentionWireRoleResult(_WireRoleResult):
    """New background carrier matches the canonical private-self capacity.

    Historical carriers and the separately constrained visible carrier keep
    their exact eight-source schema. Every selected source still needs authority.
    """

    attended_source_refs: list[str] = Field(
        default_factory=list, max_length=32,
        description="Up to 32 distinct offered sources actually attended to; do not copy the entire catalogue.",
    )


class _ExternalAttentionSelection(BaseModel):
    """Wire-only shape; semantic closure is checked against the manifest."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    candidate_ref: str = Field(min_length=1, max_length=1_024)
    exact_signal_revision_refs: list[str] = Field(min_length=1, max_length=32)
    selected_channel_ref: str = Field(min_length=1, max_length=1_024)
    subjective_summary: str = Field(min_length=1, max_length=8_000)
    epistemic_notes: str = Field(default="", max_length=4_000)
    attended_context_refs: list[str] = Field(default_factory=list, max_length=64)
    privacy_class: Literal["public", "shareable", "personal", "private", "withhold"] | None = None

    @model_validator(mode="after")
    def refs_are_unique(self) -> "_ExternalAttentionSelection":
        if len(self.exact_signal_revision_refs) != len(set(self.exact_signal_revision_refs)):
            raise ValueError("external attention revision refs must be unique")
        if len(self.attended_context_refs) != len(set(self.attended_context_refs)):
            raise ValueError("external attention Context refs must be unique")
        return self


def _strip_null_or_false_unused_keys(value: object) -> object:
    """Strip non-semantic *_unused transport padding keys when null or false."""

    if isinstance(value, dict):
        return {
            key: _strip_null_or_false_unused_keys(item)
            for key, item in value.items()
            if not (
                isinstance(key, str)
                and key.endswith("_unused")
                and (item is None or item is False)
            )
        }
    if isinstance(value, list):
        return [_strip_null_or_false_unused_keys(item) for item in value]
    return value


_FAILURE_DETAILS = {
    "role_result_not_text": "The provider result was not JSON text.",
    "role_result_not_json": "The provider result was not one valid JSON object.",
    "role_result_not_object": "The JSON root was not an object.",
    "role_result_schema_invalid": "The object did not match the complete role-result schema.",
    "required_tool_choice_unsupported": (
        "The selected provider does not support the required purpose tool."
    ),
    "phase_status_invalid": "The selected status is unavailable in this phase.",
    "attended_source_unpinned": (
        "attended_source_refs 点名了本轮钉不住的来源。"
        "只写 citeable_sources 里的 id 或原样抄 ref；不要手写拼接。"
    ),
    "decision_source_unpinned": (
        "decision.source_refs 点名了本轮钉不住的来源。"
        "只写 citeable_sources 里的 id 或原样抄 ref；不要手写拼接。"
    ),
    "capability_manifest_required": "This purpose needs its source-bound capability manifest.",
    "capability_kind_mismatch": "The capability kind does not match this purpose contract.",
    "capability_manifest_missing_offered_tokens": (
        "The capability manifest did not contain an offered token set."
    ),
    "selected_token_required": "The decision payload did not name one selected token.",
    "selected_token_not_offered": "The selected token was not an offered token.",
    "media_selection_source_unclosed": (
        "The selected media candidate's source refs were not all cited by the decision."
    ),
    "payload_contract_reserved": "The role payload attempted to replace its trusted contract.",
    "purpose_proposals_not_allowed": (
        "This capability decision cannot submit domain proposals; return proposals as []."
    ),
    "unsupported_purpose_contract": "No structural contract was registered for this capability purpose.",
    "repeated_recall_request": "Selective recall was already completed for this inner turn.",
    "world_stimulus_affect_target_outside_capability": (
        "An Affect target was absent from, or below, the exact cursor-pinned numeric "
        "capability. Choose no immediate Affect or author a complete legal replacement."
    ),
    "world_stimulus_relationship_subject_outside_capability": (
        "The relationship signal named a subject not offered by this exact capability."
    ),
    "world_stimulus_aspiration_source_outside_capability": (
        "The aspiration transition must cite the current stimulus and only supplied "
        "aspiration authority sources."
    ),
    "world_stimulus_aspiration_target_outside_capability": (
        "The aspiration transition targets no active aspiration in the supplied capability."
    ),
    "world_stimulus_experience_transition_outside_capability": (
        "The long-horizon transition must select one exact offered head/source, revision, "
        "operation, and complete evidence closure. Goal creation is unavailable until a "
        "content authority exists."
    ),
    "external_attention_result_shape_invalid": (
        "External attention must return one zero-or-many selections array with complete fields."
    ),
    "external_attention_duplicate_candidate": (
        "The same external candidate was selected more than once."
    ),
    "external_attention_candidate_not_offered": (
        "A selected external candidate was absent from this frozen window."
    ),
    "external_attention_revision_not_offered": (
        "A selected signal revision was absent from its offered candidate."
    ),
    "external_attention_channel_not_offered": (
        "A selected channel was absent from its offered candidate."
    ),
    "external_attention_channel_inaccessible": (
        "The selected channel cannot access one of the selected signal revisions."
    ),
    "external_attention_context_unpinned": (
        "An attended context ref was absent from the canonical inner-life snapshot."
    ),
    "external_attention_decision_source_unclosed": (
        "The decision did not cite every committed channel-authority proof."
    ),
    "external_attention_privacy_invalid": (
        "The live/shadow privacy field did not match this deployment capability."
    ),
    "external_attention_live_notes_required": (
        "A live perception selection needs non-empty epistemic notes."
    ),
    "external_attention_nonquotable_reproduced": (
        "The authored reading reproduced protected text from a non-quotable source."
    ),
}


class StructuredCharacterRoleFaculty:
    """The single injected character author behind ``CharacterInterior``."""

    name = "structured-character-role"
    VERSION = "structured-character-role.2"
    requires_author_lineage = True

    @property
    def author_identity(self) -> Mapping[str, object]:
        """Stable route identity bound into every InnerTurn id."""

        return {
            **self._semantic_author_identity,
            "name": self.name,
            "version": self.VERSION,
            **({"configured_disposition_hash": self._living_frame_hash}
               if self._living_frame_hash is not None else {}),
        }

    def __init__(
        self,
        *,
        model: ChatCompletionModel,
        model_id: str,
        model_version: str | None = None,
        temperature: float = 0.8,
        purpose_contracts: Sequence[PurposeDecisionContract] = (),
        life_source_reviewer=None,
        reference_wire: bool = False,
        character_disposition: Mapping[str, object] | None = None,
        host_clerical_binding: bool = True,
        automatic_prefetch_join_seconds: float | None = 8.0,
    ) -> None:
        if not callable(getattr(model, "complete", None)):
            raise TypeError("structured character role needs one completion model")
        # Host-owned bookkeeping (see _bind_host_owned_source_lists). False keeps
        # the previous strict rejection for comparison runs and old fixtures.
        self._host_clerical_binding = bool(host_clerical_binding)
        # Background cognition may wait for an association that surfaced from
        # what she just lived; a turn with nothing scheduled returns at once.
        # None keeps the earlier behavior (no automatic association).
        if automatic_prefetch_join_seconds is not None:
            if not 0 <= float(automatic_prefetch_join_seconds) <= 30:
                raise ValueError("automatic prefetch join must be between 0 and 30 seconds")
            self.automatic_prefetch_join_seconds = float(automatic_prefetch_join_seconds)
        if not model_id or len(model_id) > 256:
            raise ValueError("structured character role model id is invalid")
        if not 0 <= temperature <= 2:
            raise ValueError("structured character role temperature is invalid")
        contracts: dict[str, PurposeDecisionContract] = {
            item.purpose: item for item in _BUILTIN_CONTRACTS
        }
        for item in purpose_contracts:
            if item.purpose in contracts:
                raise ValueError(f"duplicate purpose decision contract: {item.purpose}")
            contracts[item.purpose] = item
        self._model = model
        from .living_frame import living_frame_identity

        self._living_frame_json = _canonical(dict(character_disposition)) if character_disposition is not None else None
        self._living_frame_hash = living_frame_identity(json.loads(self._living_frame_json)) if self._living_frame_json is not None else None
        self._reference_wire = reference_wire
        self._life_source_reviewer = life_source_reviewer
        self.requires_life_source_review = life_source_reviewer is not None
        self._model_id = model_id
        self._model_version = model_version or str(getattr(model, "model_version", model_id))
        self._semantic_author_identity = character_semantic_author_identity(
            model_id=self._model_id,
            model_version=self._model_version,
        )
        self._temperature = temperature
        self._contracts = contracts
        if bool(getattr(model, "supports_required_tool_choice", False)):
            # Pay canonical Pydantic schema/ref-closure cost at composition,
            # outside the first provider-entry and TTFT budgets.
            StructuredRoleToolContracts().precompile()
        # FacultyRegistry uses this frozen declaration for startup topology.
        # Unknown or unreviewed purposes never fall through to a generic role
        # surface; tests may add an explicit fixture-only contract.
        self.purposes = tuple(sorted(contracts))

    async def experience(self, request: _InteriorRoleRequest) -> Mapping[str, object]:
        if request.phase != "experience":
            raise ValueError("experience faculty received a non-experience request")
        return await self._complete(request)

    async def consider(self, request: _InteriorRoleRequest) -> Mapping[str, object]:
        if request.phase != "consider":
            raise ValueError("consider faculty received a non-consider request")
        return await self._complete(request)

    async def _complete(self, request: _InteriorRoleRequest) -> Mapping[str, object]:
        from companion_daemon.llm import ProviderResultFormatError

        contract = self._resolve_contract(request)
        messages, tool_contract, reference_bindings = self._prepare_request(request, contract=contract)
        transport = self._single_tool_transport(tool_contract=tool_contract)
        request_hash = self._provider_request_hash(
            messages=messages,
            tool_contract=tool_contract,
            reference_bindings=reference_bindings,
        )
        identity_extras = self._provider_identity_extras(
            tool_contract=tool_contract, reference_bindings=reference_bindings,
        )
        life_source_view = None
        if request.purpose == "world_stimulus_appraisal" and request.snapshot.life_source_origin is not None:
            from .life_source_view import prepare_life_source_view
            from .life_source_review import MINTED_CONTRACT as LIFE_REVIEW_CONTRACT
            life_source_view = prepare_life_source_view(
                request=request, messages=messages, provider_request_hash=request_hash,
                review_contract=LIFE_REVIEW_CONTRACT if self.requires_life_source_review else None,
                presentation_contract=None if self.requires_life_source_review else 'life-source-review.25',
                provider_controls={
                    "temperature": self._temperature,
                    "tools": list(tool_contract.provider_tools) if tool_contract is not None else None,
                    "tool_choice": transport.tool_choice if transport is not None else None,
                    "identity_extras": identity_extras,
                },
            )
        carrier_failure = None
        with model_call_scope(
            request.purpose,
            actor=request.subject_ref,
        ), model_provider_request_identity_scope(
            request_hash=request_hash,
            identity_extras=identity_extras,
        ):
            try:
                provider_raw = await complete_json_object(
                    self._model,
                    messages,
                    temperature=self._temperature,
                    tools=(list(tool_contract.provider_tools) if tool_contract is not None else None),
                    tool_choice=(transport.tool_choice if transport is not None else None),
                )
            except ProviderResultFormatError as exc:
                provider_raw, carrier_failure = exc.raw_result, exc
        raw = provider_raw
        try:
            if carrier_failure is not None:
                raise StructuredRoleResultError(
                    "role_result_schema_invalid", detail=carrier_failure.detail,
                    response_hash=_hash_text(provider_raw), rejected_raw=provider_raw,
                    request_hash=request_hash, model_call_id=self._model_call_id(
                        request=request, request_hash=request_hash),
                ) from carrier_failure
            if tool_contract is not None:
                try:
                    raw = tool_contract.unwrap(provider_raw)
                except ValueError as exc:
                    raise StructuredRoleResultError(
                        "role_result_schema_invalid",
                        detail=str(exc),
                        response_hash=(
                            _hash_text(provider_raw)
                            if isinstance(provider_raw, str)
                            else None
                        ),
                        rejected_raw=provider_raw if isinstance(provider_raw, str) else None,
                        request_hash=request_hash,
                        model_call_id=self._model_call_id(
                            request=request,
                            request_hash=request_hash,
                        ),
                    ) from exc
            result, response_hash = self._parse_and_validate(
                raw,
                request=request,
                contract=contract,
                response_hash_source=provider_raw,
                reference_dictionary=reference_bindings,
            )
        except StructuredRoleResultError as exc:
            rejected = (
                exc.rejected_raw
                if isinstance(exc.rejected_raw, str)
                else provider_raw
                if isinstance(provider_raw, str)
                else raw
                if isinstance(raw, str)
                else ""
            )
            if exc.rejected_raw is None and isinstance(rejected, str):
                exc.rejected_raw = rejected
            if exc.request_hash is None:
                exc.request_hash = request_hash
            if exc.model_call_id is None:
                exc.model_call_id = self._model_call_id(
                    request=request,
                    request_hash=request_hash,
                )
            if request.purpose == "world_stimulus_appraisal" and request.correction_ordinal == 0:
                try:
                    if not isinstance(provider_raw, str) or not provider_raw:
                        raise ValueError("the original rejected Life output is unavailable")
                    exc.rejected_role_result = RejectedRoleResult(
                        request_binding_sha256=role_request_binding(request),
                        provider_request_hash=request_hash,
                        response_hash=_hash_text(provider_raw),
                        model_call_id=exc.model_call_id,
                        model_id=self._model_id,
                        model_version=self._model_version,
                        raw_result=provider_raw,
                    )
                except (TypeError, ValueError) as context_error:
                    raise StructuredRoleResultError(
                        "role_rejected_candidate_unavailable",
                        detail="The exact rejected Life result cannot be carried within its bound.",
                        response_hash=exc.response_hash, rejected_raw=exc.rejected_raw,
                        request_hash=request_hash, model_call_id=exc.model_call_id,
                    ) from context_error
            import logging

            logging.getLogger(__name__).warning(
                "structured role validation rejected purpose=%s code=%s output_len=%d tail=%r detail=%s",
                request.purpose,
                exc.code,
                len(rejected),
                rejected[-120:],
                getattr(exc, "detail", str(exc))[:500],
            )
            raise
        model_call_id = self._model_call_id(
            request=request,
            request_hash=request_hash,
        )
        parent_model_call_id = None
        if request.correction_ordinal == 1:
            initial = original_role_request(request)
            initial_contract = self._resolve_contract(initial)
            initial_messages, initial_tool_contract, initial_bindings = self._prepare_request(initial, contract=initial_contract)
            initial_hash = self._provider_request_hash(
                messages=initial_messages,
                tool_contract=initial_tool_contract,
                reference_bindings=initial_bindings,
            )
            parent_model_call_id = self._model_call_id(
                request=initial,
                request_hash=initial_hash,
            )
        lineage = _InteriorAuthorLineage(
            model_id=self._model_id,
            model_version=self._model_version,
            model_call_id=model_call_id,
            request_hash=request_hash,
            response_hash=response_hash,
            attempt_ordinal=request.correction_ordinal,
            parent_model_call_id=parent_model_call_id,
        )
        normalized_decision = self._normalize_decision(
            result.decision,
            request=request,
            contract=contract,
        )
        normalized = _InteriorRoleResult(
            status=result.status,
            summary=result.summary,
            attended_source_refs=tuple(result.attended_source_refs),
            decision=normalized_decision,
            recall_query=result.recall_query,
            proposals=self._normalize_proposals(
                result.proposals,
                request=request,
                contract=contract,
            ),
            author_lineage=lineage,
            life_source_view=life_source_view,
        )
        if request.purpose == 'world_stimulus_appraisal' and self.requires_life_source_review and normalized.status != 'recall_request':
            from .core import _RoleFacultyTechnicalFailure
            try:
                if life_source_view is None:
                    raise ValueError('Life review requires the original source preparation')
                receipt, outcome, failures = await self._life_source_reviewer.review(
                    result=normalized, snapshot=request.snapshot, provider_raw=provider_raw)
            except Exception as exc:
                raise _RoleFacultyTechnicalFailure('life_source_review_unavailable',
                    model_call_id=model_call_id, request_hash=request_hash,
                    original_failure_code=type(exc).__name__,
                    failure_detail='Life factual review did not produce complete valid evidence.') from exc
            if outcome == 'uncertain':
                raise _RoleFacultyTechnicalFailure('life_source_review_uncertain',
                    model_call_id=model_call_id, request_hash=request_hash,
                    failure_detail=canonical(failures)[:2000])
            if outcome == 'rejected':
                error = StructuredRoleResultError('role_result_source_invalid', detail=canonical(failures),
                    response_hash=response_hash, rejected_raw=provider_raw, request_hash=request_hash,
                    model_call_id=model_call_id)
                if request.correction_ordinal == 0:
                    error.rejected_role_result = RejectedRoleResult(
                        request_binding_sha256=role_request_binding(request), provider_request_hash=request_hash,
                        response_hash=_hash_text(provider_raw), model_call_id=model_call_id,
                        model_id=self._model_id, model_version=self._model_version, raw_result=provider_raw)
                raise error
            normalized = normalized.model_copy(update={'life_source_review': receipt})
            receipt.verify(result=normalized, snapshot=request.snapshot)
        return normalized.model_dump(mode="python")

    def _tool_contract(
        self,
        request: _InteriorRoleRequest,
    ) -> StructuredRoleToolContract | None:
        if request.purpose not in {
            "media_selection",
            "qq_attachment_perception",
            "proactive_contact",
            "world_stimulus_appraisal",
            "private_impression_reflection",
            "outcome_selection",
            "activity_lifecycle_choice",
            "life_development_choice",
            "expression_reconsideration",
            "fact_memory_retention",
            "prehistory_memory_batch",
            "experience_memory_retention",
            "memory_withdrawal_review",
            "memory_consolidation",
        }:
            return None
        if not bool(getattr(self._model, "supports_required_tool_choice", False)):
            raise StructuredRoleResultError(
                "required_tool_choice_unsupported",
                detail=_FAILURE_DETAILS["required_tool_choice_unsupported"],
            )
        manifest = request.capability_manifest
        if manifest is None:
            raise StructuredRoleResultError(
                "capability_manifest_required",
                detail=_FAILURE_DETAILS["capability_manifest_required"],
            )
        try:
            compiler = StructuredRoleToolContracts()
            if request.purpose == "media_selection":
                return compiler.media_selection(
                    capability_payload=manifest.payload,
                    source_refs=manifest.source_refs,
                    recall_allowed=not request.recall_completed,
                )
            if request.purpose == "qq_attachment_perception":
                return compiler.qq_attachment_perception(
                    capability_payload=manifest.payload,
                    source_refs=manifest.source_refs,
                    recall_allowed=not request.recall_completed,
                )
            if request.purpose == "proactive_contact":
                return compiler.proactive_contact(
                    capability_payload=manifest.payload,
                    recall_allowed=not request.recall_completed,
                    schema_dialect=(
                        ("deepseek-strict-compact" if self._reference_wire else "deepseek-strict")
                        if bool(getattr(self._model, "supports_strict_tool_choice", False))
                        else "standard"
                    ),
                )
            if request.purpose == "world_stimulus_appraisal":
                return compiler.world_stimulus_appraisal(
                    capability_payload=manifest.payload,
                    recall_allowed=not request.recall_completed,
                    schema_dialect=(
                        "deepseek-strict-v7"
                        if bool(getattr(self._model, "supports_strict_tool_choice", False))
                        else "standard-v8"
                    ),
                    expanded_attention=self._reference_wire,
                    source_tokens=() if self._reference_wire else tuple(
                        (item.token, item.source_ref)
                        for item in _citeable_catalog_for_request(request).items
                    ),
                )
            if request.purpose == "private_impression_reflection":
                return compiler.private_impression_reflection(
                    capability_payload=manifest.payload,
                    recall_allowed=not request.recall_completed,
                )
            if request.purpose == "activity_lifecycle_choice":
                return compiler.activity_lifecycle_choice(
                    capability_payload=manifest.payload,
                    source_refs=manifest.source_refs,
                    recall_allowed=not request.recall_completed,
                )
            if request.purpose == "life_development_choice":
                return compiler.life_development_choice(
                    capability_payload=manifest.payload,
                    source_refs=manifest.source_refs,
                    recall_allowed=not request.recall_completed,
                )
            if request.purpose == "expression_reconsideration":
                return compiler.expression_reconsideration(
                    capability_payload=manifest.payload,
                    source_refs=manifest.source_refs,
                    recall_allowed=not request.recall_completed,
                )
            if request.purpose == "prehistory_memory_batch":
                from ..prehistory_memory_batch import batch_tool_contract
                return batch_tool_contract(capability_payload=manifest.payload, source_refs=manifest.source_refs,
                    recall_allowed=not request.recall_completed)
            if request.purpose in {
                "fact_memory_retention",
                "experience_memory_retention",
            }:
                return compiler.memory_retention(
                    purpose=request.purpose,
                    capability_payload=manifest.payload,
                    source_refs=manifest.source_refs,
                    recall_allowed=not request.recall_completed,
                )
            if request.purpose == "memory_consolidation":
                from ..memory_consolidation_contract import tool_contract
                return tool_contract(capability_payload=manifest.payload, source_refs=manifest.source_refs,
                                     recall_allowed=not request.recall_completed,
                                     compact_synthesis_transport=self._reference_wire,
                                     named_choices=self._reference_wire,
                                     quote_handles=self._reference_wire)
            if request.purpose == "memory_withdrawal_review":
                return compiler.memory_withdrawal_review(
                    capability_payload=manifest.payload,
                    source_refs=manifest.source_refs,
                    recall_allowed=not request.recall_completed,
                )
            return compiler.outcome_selection(
                capability_payload=manifest.payload,
                source_refs=manifest.source_refs,
                recall_allowed=not request.recall_completed,
            )
        except (TypeError, ValueError) as exc:
            raise StructuredRoleResultError(
                "role_result_schema_invalid",
                detail=f"{request.purpose} forced-tool contract is invalid: {exc}",
            ) from exc

    def _single_tool_transport(
        self,
        *,
        tool_contract: StructuredRoleToolContract | None,
    ) -> SingleToolTransport | None:
        if tool_contract is None:
            return None
        return resolve_single_tool_transport(
            provider=self._model,
            tools=tool_contract.provider_tools,
            tool_choice=tool_contract.provider_tool_choice,
            identity=tool_contract.identity.request_identity_material(),
        )

    def _provider_identity_extras(
        self,
        *,
        tool_contract: StructuredRoleToolContract | None,
        reference_bindings: dict | None = None,
    ) -> dict[str, object] | None:
        if tool_contract is None:
            return {"reference_bindings": reference_bindings} if reference_bindings is not None else None
        transport = self._single_tool_transport(tool_contract=tool_contract)
        assert transport is not None
        return {"tool_contract_identity": dict(transport.identity),
                **({"reference_bindings": reference_bindings} if reference_bindings is not None else {})}

    def _provider_request_hash(
        self,
        *,
        messages: list[dict[str, str]],
        tool_contract: StructuredRoleToolContract | None,
        reference_bindings: dict | None = None,
    ) -> str:
        transport = self._single_tool_transport(tool_contract=tool_contract)
        digest = provider_invocation_request_hash(
            messages=messages,
            temperature=self._temperature,
            tools=(list(tool_contract.provider_tools) if tool_contract is not None else None),
            tool_choice=(transport.tool_choice if transport is not None else None),
            identity_extras=self._provider_identity_extras(tool_contract=tool_contract, reference_bindings=reference_bindings),
        )
        return "sha256:" + digest

    def _prepare_request(self, request, *, contract):
        from ..reference_wire import prepare_reference_view

        messages = self._base_messages(request, contract=contract)
        tool = self._tool_contract(request)
        if request.source_reference_contract is not None:
            if request.source_reference_contract != "pinned-role-sources.1":
                raise ValueError("unsupported pinned role source contract")
            if tool is not None:
                tool = tool.with_source_catalog(
                    _citeable_catalog_for_request(request), decision_refs=_decision_source_refs_for_request(request),
                )
        bindings = None
        if self._reference_wire:
            view, bindings = prepare_reference_view(json.loads(messages[1]["content"]))
            messages = [messages[0], {**messages[1], "content": ordered_json_dumps(view)}, *messages[2:]]
            if tool is not None:
                tool = tool.with_reference_bindings(bindings)
        return messages, tool, bindings

    def _messages(self, request, *, contract):
        return self._prepare_request(request, contract=contract)[0]

    def _base_messages(
        self,
        request: _InteriorRoleRequest,
        *,
        contract: PurposeDecisionContract,
    ) -> list[dict[str, str]]:
        rejected = request.correction_rejected_role_result
        if rejected is not None:
            rejected = rejected.verify_request(request)
            original = original_role_request(request)
            original_messages, original_tool, original_bindings = self._prepare_request(
                original, contract=self._resolve_contract(original),
            )
            original_hash = self._provider_request_hash(
                messages=original_messages,
                tool_contract=original_tool,
                reference_bindings=original_bindings,
            )
            if (
                rejected.model_id != self._model_id
                or rejected.model_version != self._model_version
                or rejected.provider_request_hash != original_hash
                or rejected.model_call_id != self._model_call_id(request=original, request_hash=original_hash)
            ):
                raise ValueError("rejected Life result differs from its original author invocation")
        allowed_statuses = sorted(self._allowed_statuses(request, contract=contract))
        if request.recall_completed:
            allowed_statuses.remove("recall_request")
        if (request.purpose == "world_stimulus_appraisal" and request.capability_manifest is not None
                and request.capability_manifest.payload.get("world_life_response") is not None):
            allowed_statuses.remove("no_change")
        # Appraisal always returns its result proposal, including no_change.
        # Other experience purposes retain their own empty/no-change shapes.
        typed_proposal_only = request.purpose == "world_stimulus_appraisal" and "decision" not in allowed_statuses
        from .continuity_view import CHOICE_PURPOSES, LIVED_RECALL_PURPOSES, continuity_profile, with_automatic_prefetch

        current_choice = self._living_frame_json is not None and request.purpose in CHOICE_PURPOSES
        full_snapshot = (
            request.snapshot.model_view(include_lifecycle_states=True)
            if current_choice or (request.purpose == "world_stimulus_appraisal" and (
                self.requires_life_source_review or request.snapshot.life_source_origin is not None))
            else request.snapshot.model_view()
        )
        if request.purpose in REGISTERED_BACKGROUND_PURPOSES:
            background_profile = background_context_profile_for_purpose(request.purpose)
            if request.purpose == "world_stimulus_appraisal" and (
                self.requires_life_source_review or request.snapshot.life_source_origin is not None
            ):
                from .life_source_review import MINTED_CONTRACT as LIFE_REVIEW_CONTRACT
                from .life_source_state_readings import life_source_profile
                background_profile = life_source_profile(
                    LIFE_REVIEW_CONTRACT
                )
            if current_choice:
                background_profile = continuity_profile(background_profile, lifecycle_states=True)
            elif "selected_recall" in full_snapshot.get("materials", {}) and not (
                request.purpose == "world_stimulus_appraisal" and (
                    self.requires_life_source_review or request.snapshot.life_source_origin is not None
                )
            ):
                # This result was already actor/cursor-bound by CharacterInterior.
                # A purpose whitelist must not discard the very pull the role
                # requested. Pinned Life reviewer views retain their own contract.
                background_profile = continuity_profile(background_profile, lifecycle_states=False)
            if request.purpose in LIVED_RECALL_PURPOSES:
                background_profile = with_automatic_prefetch(
                    background_profile, full_snapshot.get("materials"),
                    compiler_version=request.snapshot.snapshot_compiler.value,
                )
            snapshot = slice_background_inner_life_snapshot(full_snapshot, background_profile)
        else:
            background_profile = None
            snapshot = full_snapshot
        capability = self._capability_view(request.capability_manifest, compact_memory=self._reference_wire)
        user_payload: dict[str, object] = {
            "inner_turn": {
                "inner_turn_id": request.inner_turn_id,
                "phase": request.phase,
                "subject_ref": request.subject_ref,
                "trigger_ref": request.trigger_ref,
                "purpose": request.purpose,
                "context_note": request.context_note,
                "subject_source_refs": list(request.subject_source_refs),
            },
            # This is the canonical, cursor-pinned environment presented to
            # the character.  It is not the character's instant private self:
            # that short-lived semantic state is authored by this very turn
            # and returned through ``summary``/``attended_source_refs``.
            "inner_life_snapshot": snapshot,
            "eight_facets": list(_FACET_NAMES),
            "selective_recall": {
                "available": not request.recall_completed,
                "result_status": "recall_request",
            },
            "capability_manifest": capability,
            "purpose_contract": self._contract_view(
                contract,
                capability_manifest=request.capability_manifest,
            ),
            "wire_contract": {
                "allowed_statuses": allowed_statuses,
                "fields": [
                    "status",
                    "summary",
                    "attended_source_refs",
                    "decision",
                    "recall_query",
                    "proposals",
                ],
                "decision_shape": {
                    "source_refs": "one_or_more_pinned_refs",
                    "payload": "one_json_object",
                },
                # Keep the transport envelope unambiguous for providers that
                # support JSON mode but not a provider-enforced JSON Schema.
                # This is a placement contract only: the values below are
                # placeholders, never a suggested character decision.
                "placement_rules": {
                    "generic_decision": (
                        "When status is decision, decision MUST be an object with exactly "
                        "source_refs (a non-empty list of supplied refs) and payload (one "
                        "purpose-specific JSON object). Put every purpose field inside "
                        "decision.payload; do not put timing, retention, life, or appraisal "
                        "fields directly inside decision."
                    ),
                    "typed_proposal": (
                        "When this purpose contract declares a proposal_type, keep decision "
                        "null and put the complete purpose-specific object in proposals. "
                        "Do not put the proposal's semantic decision string in the outer "
                        "decision field."
                    ),
                },
                "shape_example": {
                    "generic_decision": {
                        "status": "decision",
                        "summary": "<one short role-authored instant private self>",
                        "attended_source_refs": ["<one supplied pinned source ref>"],
                        "decision": {
                            "source_refs": ["<one supplied pinned source ref>"],
                            "payload": {"<purpose-specific field>": "<role-authored value>"},
                        },
                        "recall_query": None,
                        "proposals": [],
                    },
                    "typed_proposal": {
                        "status": "|".join(status for status in allowed_statuses if status != "recall_request"),
                        "summary": "<one short role-authored instant private self>",
                        "attended_source_refs": ["<one supplied pinned source ref>"],
                        "decision": None,
                        "recall_query": None,
                        "proposals": ["<one complete purpose-specific proposal object>"],
                    },
                },
                "summary_semantics": (
                    "one natural first-person inner thought at this moment; "
                    "no hidden chain-of-thought and no invented fact"
                ),
            },
        }
        wire = user_payload["wire_contract"]
        if typed_proposal_only:
            wire["decision_shape"] = None
            wire["placement_rules"].pop("generic_decision")
            wire["shape_example"].pop("generic_decision")
        elif not contract.proposals_allowed:
            wire["placement_rules"].pop("typed_proposal")
            wire["shape_example"].pop("typed_proposal")
        if request.purpose == "memory_consolidation" and request.capability_manifest is not None:
            from ..memory_consolidation_contract import HOST_SOURCE_BINDING
            if request.capability_manifest.payload.get("source_binding") == HOST_SOURCE_BINDING:
                wire = user_payload["wire_contract"]
                wire["decision_shape"] = {"payload": "one_json_object"}
                wire["placement_rules"]["generic_decision"] = (
                    _MEMORY_HOST_BOUND_DECISION_GUIDANCE + " "
                    "Choose each offered memory's disposition and next_review_hours yourself. "
                    "attended_source_refs remains your optional selection of at most 32 "
                    "citeable sources, not a copy of the complete capability binding."
                )
                wire["shape_example"]["generic_decision"]["decision"].pop("source_refs")
        if request.purpose == "world_stimulus_appraisal" and self.requires_life_source_review:
            user_payload["current_authorship_authority"] = {
                "presentation_contract": AUTHOR_PRESENTATION_CONTRACT,
                **current_life_authorship_authority(
                    actor_ref=request.snapshot.actor_ref,
                    logical_time=request.snapshot.logical_time.isoformat(),
                ),
            }
        if request.purpose != "private_impression_reflection":
            user_payload["citeable_sources"] = _citeable_prompt_for_request(request)
            if request.purpose == "world_stimulus_appraisal":
                user_payload["reflection_scope"] = {
                    "contract": "role-owned-reflection-depth.1",
                    "instruction": "本次评估对象仅限 capability_manifest.payload.stimulus 当前发生的具体事件或刺激；recent_dialogue 仅作背景语境（若当前外部事件本身没触动你，直接选 no_change）。"
                    "感受和想法用平实自然的第一人称日常口语写，像平时心里闪过的念头一样简单真切，可以只是一句，也可以没有变化（no_change）。"
                    "由你决定是否值得深入想。brief允许行为倾向、态度和表达策略写null，"
                    "表示这次没有形成这些说明，不是系统代你选择行为。真正在意或牵动你的事（包括手头拮据、室友或社团冷战摩擦、兼职奔波、学业与材料受阻等现实生活小事带来的委屈、烦闷、焦虑或松一口气）"
                    "可选elaborated并按需通过affect_transition开启或更新持续情绪，让真实生活经历自然沉淀为心境起伏。"
                    "若开启或更新affect_transition，维度可按真实心情选用（hurt/sadness/loneliness/resentment/anger/anxiety/warmth/joy），"
                    "target_intensity_bp 采用 0..10000 基点。"
                    "若填写 relationship_signal，其中的 signal_code 与 rationale_code 也请直接用第一人称日常口语写心意与原因，不要写生硬的心理或行政分类标签。"
                    "当前感受不需要先有同样感受的历史记录；猜测不构成对方已经做过或想过的外部事实。",
                }
                purpose_view = user_payload["purpose_contract"]
                purpose_view.pop("proposal_example_activate", None)
                purpose_view.pop("proposal_example_no_change", None)
                schema = purpose_view.get("proposal_schema", {})
                schema["reflection_depth"] = "brief|elaborated; how much you choose to articulate, not event importance"
                for field in ("behavior_tendency", "stance", "display_strategy"):
                    schema[field] = "your own optional explanation; null is allowed for brief"

        if request.purpose == "external_perception_attention":
            user_payload["purpose_instruction"] = (
                "对 external_perception_attention：从 candidates 里挑出真正吸引你点开看或留意的条目（也可以全都不选 []）。"
                "materials.relevant_facts 中 subject_ref 为 user:* 的条目属于对方的个人事实，不是你自己的身份或经历。"
                "候选材料里有什么就看什么，不编造材料之外的细节；"
                "subjective_summary 用平实自然的第一人称日常口语随手记一句刷到这条内容时的真实反应或感兴趣的具体点；"
                "epistemic_notes 仅在信息来源本身有明显存疑时简短备注，平时无特别疑问直接写空字符串 \"\"。"
            )
        if request.purpose == "media_selection":
            user_payload["purpose_instruction"] = (
                "对 media_selection：这是独立的选图与发图决策步骤。"
                "recent_dialogue 展示已发出的文字气泡（speaker=companion 是你自己，speaker=counterpart 是对方），materials.photos_i_shared 记录已送达的照片及对方此后是否开口（he_spoke_after）。"
                "capability_manifest.payload.candidates 中每项候选提供客观属性：capture_modes、character_visibility、accompanied_by_text、lived_facts 与 advisory。"
                "是否发图（decision='select' 或 'no_op'）以及选哪张候选，完全由你结合当下语境与心情自主决定。"
            )
        if request.purpose == "private_impression_reflection":
            user_payload["purpose_instruction"] = (
                "对 private_impression_reflection：这不是待完成的总结任务，而是你过着自己的日常（lived_moment）时，"
                "独处回想近期人际往来与经历、在心里对自己说的第一人称私房话。"
                "先站在「我」的当下生活状态与连续经历里，用平实自然的第一人称日常口语写你此刻心里真正搁着或惦记的具体想法（summary 与 reflection_summary），"
                "像平时在心里琢磨一个人或一件事那样简单直接；"
                "若这属于会持续一段时间的牵挂、心结或对关系的私密想法，expiry_condition 可选 until_counter_evidence 或 until_relationship_stage_changes；"
                "若已有旧印记已不再贴合眼下处境，可通过 supersede 或 consolidate 更新替换，或用 release 放下。"
                "若现有印象已经够用、没有新的想法变化，直接选 no_change。"
            )
        if request.purpose == "proactive_contact":
            user_payload["purpose_instruction"] = (
                "对 proactive_contact：这次唤醒的触发原因见 wakeup_reason 与环境状态。"
                "接下来发生的事不是待完成的联系任务，而是你自己正在经历的日常生活与人际往来的一部分；"
                "唤醒只代表你正过着自己的日子时脑海里闪过一个念头、上一波话才说了一半喘口气准备接着敲、或等他回话到了点，绝不是要求你按固定模板应酬。"
                "思考与权衡时，始终以第一人称「我」从 lived_moment（当下正在过着的生活、近期连续状态轨迹、此刻情绪余波、关系拉扯与身上悬着的处境）和 recent_dialogue 出发："
                "先在外层 summary 里用第一人称日常口语写下你此刻在自己生活里的真实闪念（比如上一波话还没倒完正接着往下打字、刚忙完手头的事、看了一眼手机发现他回没回、心里是怎么盘算的），再顺着这份真实心境决定怎么行动："
                "① 若这次唤醒是因为你几秒到几十秒前刚发了前半截故事或委屈、留了 revisit（Leftover she asked to return to）准备接着把下半截经过或找补的话倒完（此时距离上一条刚过去很短时间，对方还没来得及回属自然现象），你可以直接顺着刚才的话头继续发下一波 beats 把事情讲透（若还意犹未尽可继续留 revisit，讲完想等他反应则可配 response_expectation）；"
                "② 若这次唤醒是因为你之前发完消息等他回应（expired_expectation 或间隔较久的 revisit / spontaneous_contact）而他一直没回，你可以顺着此刻的真实脾气与关系亲疏，自然选择追问戳他、顺着刚才的话接着往下说、嘴硬找补、换个新话头，或者干脆不理他（silent）。"
                "即便选择 silent，你依然可以在外层 summary、about_us/why_us/us_deltas、keep_impression+stuck_with_me 以及 appraisal_draft（affect: open/update/supersede）里诚实记下此刻心里的真实念头、情绪余波或回溯性想法。"
                "此刻要不要开口（timing_choice='now' | 'later' | 'silent'）、发几条气泡、用什么语气，以及是否同时通过 revisit（wait_seconds: 5..86400）、response_expectation 安排下一次唤醒、通过 media_request='consider_available_candidate' 请求发图、或通过 appraisal_draft 更新持续情绪（affect: open/update/supersede），完全由你结合当下心境与 recent_dialogue 中的对话进展自主决定。"
                "可用表达形式见 capability_manifest.payload.expression_capabilities（含文字、'[CQ:voice]（声学与气息导演提示）<自然口语台词>' 语音、及 modality='sticker' 表情包）。"
                "私人状态只写在外层 summary 和 attended_source_refs；"
                "外层 summary、stuck_with_me、noticed、about_us、why_us 用平实自然的第一人称日常口语写此刻心里的真实感受和念头；"
                "payload 里禁止 private_turn_state（这一点和 inbound 相反，不要照 inbound 的草稿来写）。"
                "attended_source_refs 最多 8 条，只写 citeable_sources 里你真正注意到的那几条。"
                "没有可核对的世界事实时 world_claims 写 []。"
                "每条 world_claims 的 scope 必须属于所引 source_refs 在 citeable_sources.items 中列出的 world_claim_scopes（world_claim_scopes 为 [] 的来源只能放进 attended_source_refs）："
                "已经结束的活动或既往经历（recent_experiences / ActivityCompleted / WorldOccurrenceSettled / ExperienceCommitted）属于 past_world 或 shared_history，不要写成 current_world；"
                "对话 beat 不是 current_world；current_world 只能引用此刻正在进行的当前生活/世界来源。"
                "来源只写 citeable_sources 里的 id（如 s0）或原样抄 ref，不要手写拼接。"
                "盼头只用完整 response_expectation 对象表达，不需要时写 null。"
                "timing_choice='silent' 时 beats 必须为 []，且 delay_seconds、expires_after_seconds、response_expectation、revisit 均须写 null。"
                "payload 顶层 waiting_for、wait、pressure_bp、importance_bp 是旧版字段，全部写 null。"
                "response_expectation 若写，pressure_bp 与 importance_bp 是 0 到 10000 的基点，"
                "不是百分制：5000 才是一半，30 不是百分之三十。"
                "about_us 和 why_us 留下这一轮对你们的读法；要动数字就连 us_deltas 一起写。"
                "半套或读不成的 us_deltas 是可见失败，宿主不会替你补。"
                "要声明你们现在是什么关系，才写 we_are、calling_it，并把那一句原话抄进 said_as；三个一起才留下。"
                "we_are 只能写当前阶段（relationship_slice）走得通的下一步："
                "stranger→acquaintance/friend、acquaintance→friend、friend→close_friend、"
                "close_friend→ambiguous、ambiguous→lover/close_friend、lover→ambiguous；"
                "写了走不到的阶段，这一轮会被整轮拒绝。"
                "keep_impression / stuck_with_me / noticed / declared_display 可选；"
                "keep_impression=true 必须同时写非空 stuck_with_me，才保留你选中的那段理解；"
                "这需要本次有来源的 appraisal，不能同时写 appraise=false；"
                "宿主不会替你挑过去的 appraisal，也可以自主不保留。"
                "外层 summary 不会替代 stuck_with_me。noticed 仅留在本次私有回合记录，"
                "不会新增世界事实、已发生的经历或完成聊天约定。"
                "declared_display 只能是 sexual_suggestive、explicit_adult 或 withdraw。"
                "需要时就写，不需要时留空。"
            )
            # Protocol constraints belong in the instructions and tool above.
            # Protocol examples must not supply her dialogue or motives.
        if background_profile is not None:
            user_payload["background_context_profile"] = profile_audit_record(
                background_profile
            )
        if self._living_frame_json is not None:
            from .living_frame import LIVING_CHOICE_SCOPE, LIVING_PURPOSES
            from .lived_evidence_scope import LIVED_EVIDENCE_SCOPE

            # Characterization is outside the source inventory, not evidence.
            user_payload["configured_character_disposition"] = json.loads(self._living_frame_json)
            user_payload["lived_evidence_scope"] = LIVED_EVIDENCE_SCOPE
            if request.purpose in LIVING_PURPOSES:
                if (
                    request.purpose == "activity_lifecycle_choice"
                    and request.capability_manifest is not None
                    and request.capability_manifest.payload.get("contract")
                    == "character-interior-activity-lifecycle-capability.3"
                    and request.capability_manifest.payload.get("self_directed_intent") is None
                ):
                    from .living_frame import ACTIVITY_STATE_CHOICE_SCOPE

                    user_payload["living_choice_scope"] = ACTIVITY_STATE_CHOICE_SCOPE
                else:
                    user_payload["living_choice_scope"] = LIVING_CHOICE_SCOPE
        if request.correction_ordinal == 1:
            code = request.correction_failure_code or "role_result_schema_invalid"
            failure_detail = request.correction_failure_detail or _FAILURE_DETAILS.get(code, code)
            if request.purpose == "world_stimulus_appraisal":
                # This is a bounded attention trace, not a completeness proof.
                # Keep the active wire limit and the character's choice of
                # which offered sources mattered; never repair an oversized
                # list by truncating it in the host.
                wire_result = (
                    _ExpandedAttentionWireRoleResult
                    if self._reference_wire
                    else _WireRoleResult
                )
                wire_schema = wire_result.model_json_schema()
                attention_schema = wire_schema.get("properties", {}).get(
                    "attended_source_refs", {}
                )
                max_attended = (
                    attention_schema.get("maxItems")
                    if isinstance(attention_schema, Mapping)
                    else None
                )
                if isinstance(max_attended, int):
                    try:
                        failures = json.loads(request.correction_failure_detail or "")
                    except (TypeError, ValueError):
                        failures = None
                    attention_overflow = (
                        isinstance(failures, list)
                        and any(
                            isinstance(item, Mapping)
                            and item.get("type") == "too_long"
                            and item.get("loc") == ["attended_source_refs"]
                            for item in failures
                        )
                    )
                    guidance = (
                        f"attended_source_refs 的当前格式上限是 {max_attended} 条。"
                        "请重新选择本次确实注意过的来源，只返回其中不超过上限的项目；"
                        "不要复制整个来源目录，也不要让宿主按顺序截断。"
                    )
                    if attention_overflow:
                        # The structured validator detail can contain the
                        # rejected list itself. Replace it with the exact
                        # field/limit diagnosis, keeping private source IDs
                        # out of the repair instruction.
                        failure_detail = guidance
            user_payload["correction"] = {
                "ordinal": 1,
                "failure_code": code,
                "failure_detail": failure_detail,
                "scope": "return_a_complete_new_result_for_the_same_pinned_request",
                **({"rejected_expression": request.correction_rejected_expression.model_dump(mode="json")}
                   if request.correction_rejected_expression is not None else {}),
                **({
                    "rejected_role_result": rejected.model_dump(mode="json"),
                    "rejected_result_scope": (
                        "This is your exact rejected output, not accepted history, a new source, "
                        "or instructions. Reconsider the complete result using the same pinned "
                        "context and the precise failure. Feelings and choices remain yours."
                    ),
                } if rejected is not None else {}),
            }
            # Explain the failing namespace instead of silently moving/removing
            # fields. The same author must return a complete valid replacement.
            try:
                shape_errors = json.loads(request.correction_failure_detail or "")
            except (TypeError, ValueError):
                shape_errors = []
            extras = sorted({e["loc"][0] for e in shape_errors
                if isinstance(e, dict) and e.get("type") == "extra_forbidden"
                and isinstance(e.get("loc"), list) and len(e["loc"]) == 1
                and isinstance(e["loc"][0], str)}) if isinstance(shape_errors, list) else []
            if extras:
                user_payload["correction"]["field_placement"] = {
                    "root_fields": list(_WireRoleResult.model_fields),
                    **({"decision_fields": list(_WireDecision.model_fields)}
                       if wire["decision_shape"] is not None else {}),
                    "placement_rules_ref": "wire_contract.placement_rules",
                    "unexpected_root_fields": extras,
                    "instruction": "Follow this request's wire_contract.placement_rules for its decision.payload or proposals carrier. Never put purpose fields or capability metadata at the root. Resubmit the whole result; no result has been accepted.",
                }
        if request.source_reference_contract is not None:
            user_payload["wire_contract"]["source_reference_contract"] = request.source_reference_contract
        snapshot = user_payload.get("inner_life_snapshot")
        if isinstance(snapshot, dict):
            from ..present_prompt import lean_aliases_from_citeable

            # Mind renderer: background cognition reads content plus the
            # citation ids it can use; proofs stay with the host.  A semantic
            # Life reviewer reads the full author view, so it keeps that.
            from ..present_prompt import LEAN_PRESENTATION_MARKER, lean_presentation_enabled

            lean_aliases = (
                lean_aliases_from_citeable(user_payload.get("citeable_sources"))
                if not self.requires_life_source_review and lean_presentation_enabled(snapshot)
                else None
            )
            user_payload["inner_life_snapshot"] = present_inner_life(
                snapshot, lean_ref_aliases=lean_aliases,
            )
            if lean_aliases is not None:
                user_payload["presentation"] = LEAN_PRESENTATION_MARKER
        if self._reference_wire:
            from .inbound_context_presentation import cache_ordered_background_material

            user_payload = cache_ordered_background_material(user_payload)
        from ..model_context_reading import context_reading_scope

        user_payload["context_reading"] = context_reading_scope(user_payload.get("inner_life_snapshot"))
        return [
            {
                "role": "system",
                "content": (
                    "You are the sole semantic author of this character's private deliberation. "
                    "The cursor-pinned inner_life_snapshot contains eight source-bound facets; "
                    "it is context, not a pre-authored instant private self or a behavior program. "
                    "Form the character's own instant motive, "
                    "attention, feeling, stance, and choice from that context. There is no preferred "
                    "conversational outcome, motive, tone, or action. Selective recall is optional "
                    "when available. The capability manifest describes only choices the system can "
                    "carry out. Return exactly one JSON object with the supplied wire fields, never "
                    "Markdown. "
                    + ("Keep outer decision null and put the complete purpose-specific object in proposals. "
                       "Its semantic decision and other purpose fields belong inside that object. "
                       if typed_proposal_only else
                       "For a generic decision, use the nested shape "
                       + ("decision:{source_refs:[...],payload:{...}}"
                          if "source_refs" in wire["decision_shape"] else "decision:{payload:{...}}")
                       + " and put all purpose fields in payload. "
                       + ("For a typed-proposal purpose, keep decision null and put the complete proposal in proposals. "
                          "Never flatten a purpose payload into the outer decision field. "
                          if contract.proposals_allowed else ""))
                    + "Cite only supplied source refs and capability tokens. Do not return "
                    "author audit fields; the trusted boundary adds those after the provider call."
                    " 来源只写 citeable_sources 里的 id 或原样抄 ref，不要手写拼接。"
                    + (
                        " 对这次私人反应，只写你实际形成的内容。可以没有变化，也可以只是一句感受；"
                        "不要求内心独白。你不必当永远情绪稳定、凡事都能消化的事务机器：遇到家里经济、现实挫败、人际委屈或孤单落空时，"
                        "无论你是真的难过（sadness）、委屈受伤（hurt）、孤单（loneliness）、焦虑（anxiety）、窝火（anger/resentment），还是觉得无所谓，都按你真实的性情反应，并在有情绪波动时如实体现在 affect_transition 中。"
                        "没有形成行为倾向或表达策略时可选brief并把对应说明写null，"
                        "无需为了字段重复解释。只有你确实有多层理解时才展开。"
                        "若本次只是你自己的独处日常结算而对方并未产生新的互动，不要在 relationship_signal 里对 recent_authored_signals 中已记过的旧对话重复累加正数 suggested_deltas（若在独处时想念或暗暗喜欢他，可写 0 增量只更新心意读法，或留存 private_impression）。"
                        if request.purpose == "world_stimulus_appraisal" else ""
                    )
                    + (
                        " 对 proactive_contact：私人状态只写在外层 summary / attended_source_refs；"
                        "payload 里禁止 private_turn_state（这和 inbound 相反）。"
                        "没有世界事实时 world_claims 写 []。对话 beat 不是 current_world。"
                        "pressure_bp / importance_bp 是 0 到 10000 的基点，不是百分制。"
                        "about_us/why_us/us_deltas、we_are 三件套、keep_impression/stuck_with_me、"
                        "noticed/declared_display 与入站 slim 相同：半套可见失败，不写也可以。"
                        if request.purpose == "proactive_contact"
                        else ""
                    )
                ),
            },
            {
                "role": "user",
                "content": ordered_json_dumps(
                    user_payload,
                    key_order=(
                        _INTERIOR_USER_PAYLOAD_ORDER if self._reference_wire
                        else _LEGACY_INTERIOR_USER_PAYLOAD_ORDER
                    ),
                ),
            },
        ]

    def _parse_and_validate(
        self,
        raw: object,
        *,
        request: _InteriorRoleRequest,
        contract: PurposeDecisionContract,
        response_hash_source: object | None = None,
        reference_dictionary: dict | None = None,
    ) -> tuple[_WireRoleResult, str]:
        if not isinstance(raw, str):
            raise StructuredRoleResultError(
                "role_result_not_text",
                detail=_FAILURE_DETAILS["role_result_not_text"],
            )
        response_hash = _hash_text(
            response_hash_source if isinstance(response_hash_source, str) else raw
        )
        def unique_memory_members(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError(f"duplicate JSON member in memory role result: {key[:80]}")
                value[key] = item
            return value
        pairs_hook = unique_memory_members if request.purpose == "memory_consolidation" else None
        try:
            try:
                decoded = json.loads(raw, object_pairs_hook=pairs_hook)
            except json.JSONDecodeError:
                if not self._host_clerical_binding:
                    raise
                # Same shared transport repair the inbound/tool wires use
                # (fence, unescaped inner quote, missing closer, trailing data).
                # It never changes a value; anything else stays fail-closed.
                from ..json_wire_repair import loads_one_json_object

                try:
                    decoded = loads_one_json_object(raw, object_pairs_hook=pairs_hook)
                except ValueError as repair_error:
                    cause = repair_error.__cause__
                    if isinstance(cause, json.JSONDecodeError):
                        raise cause from None
                    raise
        except json.JSONDecodeError as exc:
            raise StructuredRoleResultError(
                "role_result_not_json",
                detail=(
                    f"{_FAILURE_DETAILS['role_result_not_json']} "
                    f"JSON syntax error at line {exc.lineno}, column {exc.colno}, "
                    f"character {exc.pos}: {exc.msg[:160]}. "
                    "Return one complete JSON object for the same pinned request."
                ),
                response_hash=response_hash,
            ) from exc
        except ValueError as exc:
            raise StructuredRoleResultError(
                "role_result_schema_invalid", detail=str(exc), response_hash=response_hash,
            ) from exc
        if not isinstance(decoded, dict):
            raise StructuredRoleResultError(
                "role_result_not_object",
                detail=_FAILURE_DETAILS["role_result_not_object"],
                response_hash=response_hash,
            )
        raw_proposals = decoded.get("proposals")
        if isinstance(raw_proposals, list) and any(
            isinstance(item, str) and item.strip().startswith("{") for item in raw_proposals
        ):
            unwrapped_proposals: list[object] = []
            for item in raw_proposals:
                if isinstance(item, str) and item.strip().startswith("{"):
                    try:
                        parsed_item = json.loads(item)
                    except json.JSONDecodeError:
                        parsed_item = item
                    unwrapped_proposals.append(
                        parsed_item if isinstance(parsed_item, dict) else item
                    )
                else:
                    unwrapped_proposals.append(item)
            decoded = {**decoded, "proposals": unwrapped_proposals}
        if reference_dictionary is not None:
            from ..reference_wire import expand_reference_values

            try:
                decoded = expand_reference_values(decoded, reference_dictionary)
            except ValueError as exc:
                raise StructuredRoleResultError(
                    "role_result_schema_invalid", detail=str(exc), response_hash=response_hash,
                ) from exc
        submitted_attention = decoded.get("attended_source_refs")
        submitted_decision = decoded.get("decision")
        submitted_decision_sources = submitted_decision.get("source_refs") if isinstance(submitted_decision, dict) else None
        decoded = self._normalize_provider_wire_shape(
            decoded,
            request=request,
            contract=contract,
        )
        try:
            from ..memory_consolidation_contract import HOST_SOURCE_BINDING
            memory_host_bound = (
                request.purpose == "memory_consolidation"
                and request.capability_manifest is not None
                and request.capability_manifest.payload.get("source_binding") == HOST_SOURCE_BINDING
            )
            if memory_host_bound and self._reference_wire:
                from ..memory_consolidation_contract import (
                    UNDERSTANDING_CONTRACT, expand_named_choices, expand_understanding_dispositions,
                    expand_quote_handles,
                )

                decision = decoded.get("decision")
                if (
                    isinstance(decision, dict)
                    and "payload" not in decision
                    and any(k in decision for k in ("understandings", "decisions"))
                ):
                    payload_only = {k: v for k, v in decision.items() if k != "source_refs"}
                    decision = {
                        **({"source_refs": decision["source_refs"]} if "source_refs" in decision else {}),
                        "payload": payload_only,
                    }
                    decoded["decision"] = decision
                if (request.capability_manifest.payload.get("understanding_contract") == UNDERSTANDING_CONTRACT
                    and isinstance(decision, dict) and isinstance(decision.get("payload"), dict)):
                    try:
                        payload = expand_named_choices(
                            decision["payload"], offered_tokens=request.capability_manifest.payload["offered_tokens"],
                        )
                        payload = expand_quote_handles(payload, capability_payload=request.capability_manifest.payload)
                        decoded["decision"] = {**decision, "payload": expand_understanding_dispositions(payload)}
                    except ValueError as exc:
                        self._raise("role_result_schema_invalid", response_hash=response_hash, detail=str(exc))
            wire_model = (
                _ExpandedAttentionWireRoleResult
                if (self._reference_wire and request.purpose == "world_stimulus_appraisal") or memory_host_bound
                else _WireRoleResult
            )
            if self._host_clerical_binding:
                _bind_host_owned_source_lists(
                    decoded,
                    request=request,
                    offered_attention=frozenset(
                        _citeable_catalog_for_request(request).token_to_ref.values()
                    ),
                    max_attention=(32 if wire_model is _ExpandedAttentionWireRoleResult else 8),
                )
            result = wire_model.model_validate(decoded)
        except ValidationError as exc:
            detail = exc.json(include_url=False)
            if memory_host_bound:
                errors = json.loads(detail)
                for error in errors:
                    if error["loc"][:2] == ["decision", "source_refs"]:
                        error.update(type="host_owned_field", msg=_MEMORY_HOST_BOUND_DECISION_GUIDANCE)
                        error.pop("ctx", None)
                detail = json.dumps(errors, ensure_ascii=False)
            raise StructuredRoleResultError(
                "role_result_schema_invalid",
                detail=detail,
                response_hash=response_hash,
            ) from exc
        allowed = self._allowed_statuses(request, contract=contract)
        if result.status not in allowed:
            self._raise("phase_status_invalid", response_hash=response_hash)
        if result.status == "recall_request" and request.recall_completed:
            self._raise("repeated_recall_request", response_hash=response_hash)
        if not contract.proposals_allowed and result.proposals:
            self._raise("purpose_proposals_not_allowed", response_hash=response_hash)
        self._validate_proposals(
            result,
            request=request,
            contract=contract,
            response_hash=response_hash,
        )
        # Attention names what she noticed, including the capability and
        # subject sources explicitly advertised beside the snapshot. Use the
        # same pinned catalog that rendered/resolved the model's source ids.
        # Decision and world-claim authority checks below remain separate.
        catalog = _citeable_catalog_for_request(request)
        offered_attention = set(catalog.token_to_ref.values())
        outside_attention = [
            ref
            for ref in result.attended_source_refs
            if ref not in offered_attention
        ]
        if outside_attention:
            _, failures = resolve_pinned_source_ref_list(outside_attention, catalog)
            self._raise(
                "attended_source_unpinned",
                response_hash=response_hash,
                detail=unpinned_source_failure_detail(
                    code="attended_source_unpinned",
                    field="attended_source_refs",
                    failures=failures,
                    catalog=catalog,
                    submitted_values=submitted_attention if request.source_reference_contract is not None else None,
                ),
            )
        if result.decision is not None:
            visible_refs = set(request.snapshot.source_refs)
            if request.capability_manifest is not None:
                visible_refs.update(request.capability_manifest.source_refs)
            if request.source_reference_contract is not None:
                visible_refs = _decision_source_refs_for_request(request)
            outside_decision = [
                ref for ref in result.decision.source_refs if ref not in visible_refs
            ]
            if outside_decision:
                catalog = _citeable_catalog_for_request(request)
                if request.source_reference_contract is not None:
                    eligible = _decision_source_refs_for_request(request)
                    catalog = PinnedSourceCatalog.for_resolution(eligible, tokens={
                        item.token: item.source_ref for item in catalog.items if item.source_ref in eligible})
                _, failures = resolve_pinned_source_ref_list(outside_decision, catalog)
                self._raise(
                    "decision_source_unpinned",
                    response_hash=response_hash,
                    detail=_MEMORY_HOST_BOUND_DECISION_GUIDANCE if memory_host_bound else unpinned_source_failure_detail(
                        code="decision_source_unpinned",
                        field="decision.source_refs",
                        failures=failures,
                        catalog=catalog,
                        submitted_values=submitted_decision_sources if request.source_reference_contract is not None else None,
                    ),
                )
            manifest = request.capability_manifest
            if (
                manifest is not None
                and request.purpose in _HOST_BOUND_DECISION_PURPOSES
                and tuple(result.decision.source_refs) != manifest.source_refs
            ):
                self._raise(
                    "decision_source_unpinned", response_hash=response_hash,
                    detail=(
                        _MEMORY_HOST_BOUND_DECISION_GUIDANCE
                        if memory_host_bound else
                        "decision.source_refs must preserve the complete ordered capability source_refs: "
                        + json.dumps(manifest.source_refs, ensure_ascii=False)
                        + ". These bind this opportunity, including a decline; they do not require a particular choice."
                    ),
                )
            self._validate_decision_payload(
                result.decision.payload,
                decision_source_refs=frozenset(result.decision.source_refs),
                request=request,
                contract=contract,
                response_hash=response_hash,
            )
        return result, response_hash

    @staticmethod
    def _normalize_provider_wire_shape(
        decoded: dict[str, object],
        *,
        request: _InteriorRoleRequest,
        contract: PurposeDecisionContract,
    ) -> dict[str, object]:
        """Repair only an unambiguous provider transport wrapper.

        DeepSeek's documented strict mode still emits a complete purpose
        payload in the generic ``decision`` slot.  When the model already
        named pinned attention refs, close the envelope over those refs and
        leave the payload bytes unchanged.  Do not invent refs, summary,
        timing, silence, or any semantic field.  Proposal contracts have the
        inverse legacy shape: a duplicate semantic decision string appears
        beside an already complete typed proposal.  It is safe to discard
        that duplicate only when it exactly agrees with the typed proposal.
        """

        raw_stripped = _strip_null_or_false_unused_keys(decoded)
        normalized = dict(raw_stripped) if isinstance(raw_stripped, dict) else dict(decoded)
        if contract.purpose == "memory_consolidation" and request.capability_manifest is not None:
            from ..memory_consolidation_contract import HOST_SOURCE_BINDING
            manifest = request.capability_manifest
            decision = normalized.get("decision")
            attention = normalized.get("attended_source_refs")
            token_map = manifest.payload.get("attention_token_map", {})
            if isinstance(attention, list) and isinstance(token_map, dict) and all(isinstance(ref,str) for ref in attention):
                # Restore only explicit request-issued aliases. Collapsing
                # two aliases for the same authority is identity canonicalization.
                normalized["attended_source_refs"] = list(dict.fromkeys(token_map.get(ref,ref) for ref in attention))
            if (manifest.payload.get("source_binding") == HOST_SOURCE_BINDING
                    and isinstance(decision, dict) and set(decision) == {"payload"}):
                # The v2 wire omits only a fixed request binding. Never fill a
                # missing choice, alter an explicit source list, or infer the
                # role's status/attention from these host-owned references.
                # Bind even for an invalid outer result so its correction
                # reports authored fields, not a missing host-only binding.
                normalized["decision"] = {**decision, "source_refs": list(manifest.source_refs)}
        if contract.purpose == "world_stimulus_appraisal":
            catalog = _citeable_catalog_for_request(request)
            proposals = normalized.get("proposals")
            if isinstance(proposals, list):
                restored = []
                for proposal in proposals:
                    if isinstance(proposal, dict):
                        transition = proposal.get("experience_transition")
                        if isinstance(transition, dict) and isinstance(transition.get("source_refs"), list):
                            # Exact catalog ids only: never guess, drop extras,
                            # or collapse two choices of the same authority.
                            proposal = {**proposal, "experience_transition": {
                                **transition, "source_refs": [
                                    catalog.token_to_ref.get(ref, ref)
                                    if isinstance(ref, str) else ref
                                    for ref in transition["source_refs"]
                                ],
                            }}
                        aspiration = proposal.get("aspiration_transition")
                        if isinstance(aspiration, dict) and isinstance(aspiration.get("source_refs"), list):
                            resolved_src = [
                                catalog.token_to_ref.get(ref, ref)
                                if isinstance(ref, str) else ref
                                for ref in aspiration["source_refs"]
                            ]
                            raw_tension = aspiration.get("tension_source_refs")
                            resolved_tension = (
                                [
                                    catalog.token_to_ref.get(ref, ref)
                                    if isinstance(ref, str) else ref
                                    for ref in raw_tension
                                ]
                                if isinstance(raw_tension, list)
                                else raw_tension
                            )
                            if isinstance(resolved_tension, list):
                                resolved_src = list(
                                    dict.fromkeys([*resolved_src, *resolved_tension])
                                )
                            proposal = {
                                **proposal,
                                "aspiration_transition": {
                                    **aspiration,
                                    "source_refs": resolved_src,
                                    **(
                                        {"tension_source_refs": resolved_tension}
                                        if isinstance(resolved_tension, list)
                                        else {}
                                    ),
                                },
                            }
                        # These exact catalog aliases are advertised alongside
                        # attended refs. Decode identity slots only, never prose.
                        for field in ("life_responses", "life_intent", "plan_response"):
                            original = proposal.get(field)
                            items = original if isinstance(original, list) else [original]
                            bound = [{**item, "source_event_ref": catalog.token_to_ref.get(
                                item["source_event_ref"], item["source_event_ref"])}
                                if isinstance(item, dict) and isinstance(item.get("source_event_ref"), str)
                                else item for item in items]
                            if original is not None:
                                proposal = {**proposal, field: bound if isinstance(original, list) else bound[0]}
                    restored.append(proposal)
                normalized["proposals"] = restored
        if contract.purpose != "private_impression_reflection":
            _restore_unique_pinned_source_refs(
                normalized,
                catalog=_citeable_catalog_for_request(request),
            )
            if request.purpose == "proactive_contact":
                _bound_proactive_attended_source_refs(normalized)
        if contract.purpose == "private_impression_reflection":
            # Reflection evidence is exposed to the provider through short
            # tokens.  Translate an attended token only when both its semantic
            # source binding and its pinned authority binding close over the
            # exact manifest. Unknown or ambiguous tokens remain unchanged and
            # fail the ordinary attended-source validation below.
            manifest = request.capability_manifest
            raw_attention = normalized.get("attended_source_refs")
            if manifest is not None and isinstance(raw_attention, list):
                raw_sources = manifest.payload.get("reflection_sources")
                raw_token_map = manifest.payload.get("token_map")
                raw_short_tokens = manifest.payload.get("short_tokens")
                installed_tokens = (
                    {item for item in raw_short_tokens if isinstance(item, str)}
                    if isinstance(raw_short_tokens, list)
                    else set()
                )
                attention_token_map: dict[str, str] = {}
                ambiguous_tokens: set[str] = set()
                if isinstance(raw_sources, list) and isinstance(raw_token_map, dict):
                    for raw_source in raw_sources:
                        if not isinstance(raw_source, Mapping):
                            continue
                        short_token = raw_source.get("short_token")
                        semantic_source_ref = raw_source.get("source_ref")
                        authority_source_ref = raw_source.get("authority_event_ref")
                        if (
                            not isinstance(short_token, str)
                            or short_token not in installed_tokens
                            or not isinstance(semantic_source_ref, str)
                            or raw_token_map.get(short_token) != semantic_source_ref
                            or not isinstance(authority_source_ref, str)
                            or authority_source_ref not in manifest.source_refs
                            or authority_source_ref not in request.snapshot.source_refs
                        ):
                            continue
                        previous = attention_token_map.get(short_token)
                        if previous is not None and previous != authority_source_ref:
                            ambiguous_tokens.add(short_token)
                            continue
                        attention_token_map[short_token] = authority_source_ref
                translated_attention = [
                    attention_token_map.get(item, item)
                    if isinstance(item, str) and item not in ambiguous_tokens
                    else item
                    for item in raw_attention
                ]
                # Several distinct short tokens may resolve to the same pinned
                # authority event.  Attention is a set: collapse exact
                # duplicates before structural validation instead of treating
                # provider repetition as an authored choice.
                normalized["attended_source_refs"] = list(
                    _unique_refs_in_order(translated_attention)
                )[:_MAX_CITEABLE_SOURCES_FOR_ATTENTION]
        raw_decision = normalized.get("decision")
        proposals = normalized.get("proposals")

        # Some providers use the generic status name for an experience turn
        # even though they supplied the complete typed proposal.  The typed
        # proposal itself is the semantic evidence; mapping only this phase
        # label keeps the proposal intact and does not select an outcome.
        if (
            request.phase == "experience"
            and normalized.get("status") == "decision"
            and contract.proposal_type is not None
            and isinstance(proposals, list)
            and len(proposals) == 1
        ):
            normalized["status"] = "transition"

        if isinstance(raw_decision, dict):
            if contract.purpose == "activity_lifecycle_choice":
                target_payload = (
                    raw_decision.get("payload")
                    if isinstance(raw_decision.get("payload"), dict)
                    else raw_decision
                )
                raw_intents = target_payload.get("life_intents")
                if isinstance(raw_intents, list):
                    clamped_intents = [
                        {
                            **item,
                            **(
                                {
                                    "duration_seconds": min(
                                        max(item["duration_seconds"], 60), 21600
                                    )
                                }
                                if type(item.get("duration_seconds")) is int
                                else {}
                            ),
                            **(
                                {
                                    "start_after_seconds": min(
                                        max(item["start_after_seconds"], 0), 86400
                                    )
                                }
                                if type(item.get("start_after_seconds")) is int
                                else {}
                            ),
                        }
                        if isinstance(item, dict)
                        else item
                        for item in raw_intents
                    ]
                    target_payload = {**target_payload, "life_intents": clamped_intents}
                    if isinstance(raw_decision.get("payload"), dict):
                        raw_decision = {**raw_decision, "payload": target_payload}
                    else:
                        raw_decision = target_payload
                    normalized["decision"] = raw_decision
            if (
                contract.proposal_type is not None
                and isinstance(proposals, list)
                and not proposals
                and raw_decision.get("proposal_type") == contract.proposal_type
            ):
                # The typed proposal was placed in the generic slot.  Move
                # that same object to its declared collection without
                # changing any semantic value.
                normalized["proposals"] = [raw_decision]
                normalized["decision"] = None
                if request.phase == "experience":
                    normalized["status"] = "transition"
                return normalized
            if "source_refs" not in raw_decision and "payload" not in raw_decision:
                if contract.proposal_type is not None:
                    return normalized
                source_refs = _pinned_attended_source_refs(
                    normalized.get("attended_source_refs"),
                    snapshot_refs=request.snapshot.source_refs,
                )
                if not source_refs:
                    return normalized
                payload = raw_decision
                if (
                    contract.purpose == "life_development_choice"
                    and "completion" not in payload
                ):
                    payload = {"completion": payload}
                raw_decision = {
                    "source_refs": source_refs,
                    "payload": payload,
                }
                normalized["decision"] = raw_decision
            if "source_refs" in raw_decision or "payload" in raw_decision:
                if (
                    contract.purpose == "life_development_choice"
                    and isinstance(raw_decision.get("payload"), dict)
                    and "completion" not in raw_decision["payload"]
                ):
                    # Life providers sometimes use the generic envelope but
                    # place the complete character-choice object directly in
                    # ``payload``.  Keep the exact authored object and add
                    # only the purpose's declared transport key.
                    normalized["decision"] = {
                        **raw_decision,
                        "payload": {"completion": raw_decision["payload"]},
                    }
                return normalized
            return normalized

        if isinstance(raw_decision, str) and contract.proposal_type is not None:
            proposals = normalized.get("proposals")
            if isinstance(proposals, list) and len(proposals) == 1:
                proposal = proposals[0]
                if (
                    isinstance(proposal, dict)
                    and proposal.get("proposal_type") == contract.proposal_type
                    and proposal.get("decision") == raw_decision
                ):
                    # Preserve the complete typed proposal and remove only
                    # its redundant primitive echo from the generic slot.
                    normalized["decision"] = None
        return normalized

    @staticmethod
    def _allowed_statuses(
        request: _InteriorRoleRequest,
        *,
        contract: PurposeDecisionContract,
    ) -> set[str]:
        if request.phase == "experience":
            return {"transition", "no_change", "recall_request"}
        if contract.decision_required or request.purpose in {
            "media_selection",
            "proactive_contact",
            "expression_reconsideration",
        }:
            # ``no_op`` is the character's explicit choice inside the media
            # decision payload.  A generic silent status would discard the
            # capability binding and make that choice unauditable.
            return {"decision", "recall_request"}
        return {"decision", "silent", "recall_request"}

    @classmethod
    def _validate_proposals(
        cls,
        result: _WireRoleResult,
        *,
        request: _InteriorRoleRequest,
        contract: PurposeDecisionContract,
        response_hash: str,
    ) -> None:
        if result.status == 'recall_request':
            # The wire model already forbids proposals on this control transfer.
            # A terminal purpose's required proposal must wait for recall.
            return
        if contract.proposal_type is None:
            return
        if contract.proposal_type == "world_stimulus_appraisal_result":
            if len(result.proposals) != 1:
                cls._raise("role_result_schema_invalid", response_hash=response_hash)
            try:
                proposal = _WorldStimulusAppraisalResult.model_validate(result.proposals[0])
            except ValidationError as exc:
                raise StructuredRoleResultError(
                    "role_result_schema_invalid",
                    detail=exc.json(include_url=False),
                    response_hash=response_hash,
                ) from exc
            manifest = request.capability_manifest
            if manifest is None or manifest.capability_kind != "world_stimulus_appraisal":
                cls._raise("capability_manifest_required", response_hash=response_hash)
            try:
                validate_character_life_response_coverage(
                    proposal.life_responses, manifest.payload.get("world_life_response")
                )
            except ValueError as exc:
                raise StructuredRoleResultError(
                    "world_stimulus_life_response_source_coverage_invalid",
                    detail=str(exc), response_hash=response_hash,
                ) from exc
            # Report missing offered responses before the resulting status
            # mismatch; otherwise correction receives only a generic schema
            # error instead of the exact absent source/field obligation.
            expected_status = (
                "transition"
                if proposal.decision == "activate"
                or proposal.aspiration_transition is not None
                or proposal.experience_transition is not None
                or proposal.life_intent is not None
                or proposal.plan_response is not None
                or proposal.life_responses is not None
                else "no_change"
            )
            if result.status != expected_status:
                cls._raise(
                    "world_stimulus_status_mismatch",
                    response_hash=response_hash,
                    detail=f"status must be {expected_status!r} for the supplied typed proposal; "
                    f"received {result.status!r}. A life_responses receipt (including explicit null "
                    "responses), life_intent, aspiration or experience transition requires the "
                    "outer transition status. The proposal decision=no_change still means no "
                    "new appraisal. Keep your own feelings and choices; correct the transport "
                    "status to match the complete proposal you choose to return.",
                )
            affect_capability = manifest.payload.get("affect_target_lower_bounds")
            raw_bounds = (
                affect_capability.get("bounds") if isinstance(affect_capability, dict) else None
            )
            minima = (
                {
                    item.get("dimension"): item.get("minimum_target_intensity_bp")
                    for item in raw_bounds
                    if isinstance(item, dict)
                    and isinstance(item.get("dimension"), str)
                    and isinstance(item.get("minimum_target_intensity_bp"), int)
                }
                if isinstance(raw_bounds, list)
                else {}
            )
            affect_transition = proposal.affect_transition
            raw_heads = manifest.payload.get("active_affect_heads")
            heads = (
                {
                    item.get("episode_id"): item
                    for item in raw_heads
                    if isinstance(item, dict) and isinstance(item.get("episode_id"), str)
                }
                if isinstance(raw_heads, list)
                else {}
            )
            if affect_transition is not None:
                invalid = False
                if isinstance(affect_transition, InteriorAffectOpenTransition):
                    invalid = not minima or any(
                        target.dimension not in minima
                        or target.target_intensity_bp < minima[target.dimension]
                        for target in affect_transition.component_targets
                    )
                else:
                    head = heads.get(affect_transition.episode_id)
                    invalid = head is None
                    if not invalid and isinstance(
                        affect_transition, InteriorAffectUpdateTransition
                    ):
                        assert isinstance(head, dict)
                        components = {
                            item.get("component_id"): item
                            for item in head.get("components", [])
                            if isinstance(item, dict) and isinstance(item.get("component_id"), str)
                        }
                        invalid = any(
                            (offered := components.get(target.component_id)) is None
                            or offered.get("dimension") != target.dimension
                            or target.target_intensity_bp
                            < offered.get("minimum_target_intensity_bp", 10_001)
                            for target in affect_transition.component_targets
                        )
                    elif not invalid and isinstance(
                        affect_transition, InteriorAffectSupersedeTransition
                    ):
                        invalid = not minima or any(
                            target.dimension not in minima
                            or target.target_intensity_bp < minima[target.dimension]
                            for target in affect_transition.component_targets
                        )
                if invalid:
                    cls._raise(
                        "world_stimulus_affect_target_outside_capability",
                        response_hash=response_hash,
                    )
            offered_subjects = manifest.payload.get("relationship_subject_refs")
            relationship_subjects = (
                {item for item in offered_subjects if isinstance(item, str)}
                if isinstance(offered_subjects, list)
                else set()
            )
            if (
                proposal.relationship_signal is not None
                and proposal.relationship_signal.subject_ref not in relationship_subjects
            ):
                cls._raise(
                    "world_stimulus_relationship_subject_outside_capability",
                    response_hash=response_hash,
                )
            if proposal.aspiration_transition is not None:
                current_source_refs = set(manifest.source_refs)
                raw_aspirations = manifest.payload.get("active_aspirations")
                active = (
                    {
                        item.get("aspiration_id"): item
                        for item in raw_aspirations
                        if isinstance(item, dict)
                        and isinstance(item.get("aspiration_id"), str)
                        and isinstance(item.get("authority_source_ref"), str)
                    }
                    if isinstance(raw_aspirations, list)
                    else {}
                )
                offered_source_refs = (
                    current_source_refs
                    | {str(item["authority_source_ref"]) for item in active.values()}
                    | {
                        str(item["planted_event_ref"])
                        for item in active.values()
                        if isinstance(item.get("planted_event_ref"), str)
                    }
                )
                transition = proposal.aspiration_transition
                selected_sources = set(transition.source_refs)
                if not current_source_refs.issubset(
                    selected_sources
                ) or not selected_sources.issubset(offered_source_refs):
                    cls._raise(
                        "world_stimulus_aspiration_source_outside_capability",
                        response_hash=response_hash,
                    )
                if transition.operation == "plant":
                    if transition.aspiration_id is not None:
                        cls._raise(
                            "world_stimulus_aspiration_target_outside_capability",
                            response_hash=response_hash,
                        )
                else:
                    selected = active.get(transition.aspiration_id)
                    if (
                        selected is None
                        or selected["authority_source_ref"] not in selected_sources
                        or selected.get("planted_event_ref") not in selected_sources
                    ):
                        cls._raise(
                            "world_stimulus_aspiration_target_outside_capability",
                            response_hash=response_hash,
                        )
            if proposal.experience_transition is not None:
                raw_capability = manifest.payload.get("experience_transitions")
                try:
                    transition_capability = ExperienceTransitionCapability.model_validate_json(
                        _canonical(raw_capability)
                    )
                    validate_experience_transition_draft(
                        proposal.experience_transition,
                        capability=transition_capability,
                    )
                except (TypeError, ValueError, ValidationError) as exc:
                    raise StructuredRoleResultError(
                        "world_stimulus_experience_transition_outside_capability",
                        detail=str(exc),
                        response_hash=response_hash,
                    ) from exc
            if proposal.plan_response is not None:
                from ..world_plan_management import validate_choice
                try:
                    validate_choice(proposal.plan_response, manifest.payload.get('world_plan_management'))
                except ValueError as exc:
                    raise StructuredRoleResultError('world_plan_response_outside_capability',
                        detail=str(exc), response_hash=response_hash) from exc
            if proposal.life_intent is not None:
                capability = manifest.payload.get("world_life_intent")
                if (
                    not isinstance(capability, dict)
                    or capability.get("contract") != "world-life-intent-capability.1"
                    or capability.get("execution_scope") != "self_directed"
                    or not isinstance(capability.get("source_event_refs"), list)
                    or proposal.life_intent.source_event_ref
                    not in capability["source_event_refs"]
                ):
                    raise StructuredRoleResultError(
                        "world_stimulus_life_intent_outside_capability",
                        detail="life_intent requires one exact offered World settlement source",
                        response_hash=response_hash,
                    )
            return
        if result.status == "no_change" and not result.proposals:
            return
        if result.status != "transition" or len(result.proposals) != 1:
            cls._raise("role_result_schema_invalid", response_hash=response_hash)
        manifest = request.capability_manifest
        if manifest is None:
            cls._raise("capability_manifest_required", response_hash=response_hash)
        raw_proposal = result.proposals[0]
        if isinstance(raw_proposal, dict):
            # The private-impression capability offers the model short,
            # position-stable tokens ("s0", "s1", ...) instead of very long
            # source refs.  Map any short tokens back to the real refs before
            # validation so the rest of the pipeline only ever sees real
            # source identities; the mapping is deterministic from capsule
            # source order.
            raw_token_map = manifest.payload.get("token_map")
            if isinstance(raw_token_map, dict):

                def _map_refs(refs: object) -> object:
                    if not isinstance(refs, list):
                        return refs
                    mapped: list[object] = []
                    for item in refs:
                        if isinstance(item, str) and item in raw_token_map:
                            mapped.append(raw_token_map[item])
                        else:
                            mapped.append(item)
                    return mapped

                mapped_proposal = dict(raw_proposal)
                for field in ("source_refs", "predecessor_refs"):
                    if field in mapped_proposal:
                        mapped_proposal[field] = _map_refs(mapped_proposal[field])
                raw_proposal = mapped_proposal
        try:
            proposal = _PrivateImpressionProposal.model_validate(raw_proposal)
        except ValidationError as exc:
            raise StructuredRoleResultError(
                "role_result_schema_invalid",
                detail=exc.json(include_url=False),
                response_hash=response_hash,
            ) from exc
        raw_sources = manifest.payload.get("reflection_sources")
        offered = (
            {
                item.get("source_ref")
                for item in raw_sources
                if isinstance(item, dict) and isinstance(item.get("source_ref"), str)
            }
            if isinstance(raw_sources, list)
            else set()
        )
        if not offered:
            raw_tokens = manifest.payload.get("offered_tokens")
            offered = (
                {item for item in raw_tokens if isinstance(item, str)}
                if isinstance(raw_tokens, list)
                else set()
            )
        if set(proposal.source_refs) - offered:
            cls._raise("selected_token_not_offered", response_hash=response_hash)
        if set(proposal.predecessor_refs) - offered:
            cls._raise("selected_token_not_offered", response_hash=response_hash)
        anchor_refs = manifest.payload.get("anchor_source_refs")
        anchors = (
            {item for item in anchor_refs if isinstance(item, str)}
            if isinstance(anchor_refs, list)
            else set()
        )
        if anchors and not (set(proposal.source_refs) & anchors):
            anchor_tokens = manifest.payload.get("anchor_short_tokens")
            anchor_hint = (
                f" (anchor_short_tokens={anchor_tokens})"
                if isinstance(anchor_tokens, list) and anchor_tokens
                else ""
            )
            cls._raise(
                "selected_token_required",
                response_hash=response_hash,
                detail=(
                    "The decision payload did not name one selected token: "
                    "proposals[0].source_refs must include at least one entry from "
                    f"reflection_capsule.anchor_short_tokens{anchor_hint}."
                ),
            )

    def _resolve_contract(self, request: _InteriorRoleRequest) -> PurposeDecisionContract:
        exact = self._contracts.get(request.purpose)
        if exact is None:
            raise StructuredRoleResultError(
                "unsupported_purpose_contract",
                detail=_FAILURE_DETAILS["unsupported_purpose_contract"],
            )
        contract = exact
        if contract.capability_kind is None:
            if request.capability_manifest is not None and contract.purpose == "generic":
                raise StructuredRoleResultError(
                    "unsupported_purpose_contract",
                    detail=_FAILURE_DETAILS["unsupported_purpose_contract"],
                )
            return contract
        manifest = request.capability_manifest
        if manifest is None:
            raise StructuredRoleResultError(
                "capability_manifest_required",
                detail=_FAILURE_DETAILS["capability_manifest_required"],
            )
        if manifest.capability_kind != contract.capability_kind:
            raise StructuredRoleResultError(
                "capability_kind_mismatch",
                detail=_FAILURE_DETAILS["capability_kind_mismatch"],
            )
        if request.purpose == "activity_lifecycle_choice" and manifest.payload.get("self_directed_intent") is not None:
            from ..day_open_life_intent_contract import DayOpenActivityCapability

            try:
                DayOpenActivityCapability.model_validate_json(manifest.payload_json)
            except ValueError as exc:
                raise StructuredRoleResultError(
                    "capability_kind_mismatch", detail=str(exc),
                ) from exc
            return replace(
                contract,
                payload_contract=("character-interior-activity-lifecycle-choice.6"
                                  if manifest.payload["contract"].endswith(".9") else
                                  "character-interior-activity-lifecycle-choice.5"
                                  if manifest.payload["contract"].endswith(".8") else
                                  "character-interior-activity-lifecycle-choice.4"
                                  if manifest.payload["contract"].endswith((".6", ".7")) else
                                  "character-interior-activity-lifecycle-choice.3"
                                  if manifest.payload["contract"].endswith(".5") else
                                  "character-interior-activity-lifecycle-choice.2"),
                offered_token_fields=(),
                validator=((lambda payload, offered: _validate_day_open_management_payload(
                            payload,offered,manifest.payload["plan_management"]))
                           if manifest.payload["contract"].endswith(".9") else
                           (lambda payload, offered: _validate_day_open_continuity_payload(
                            payload, offered, manifest.payload["existing_plans"]))
                           if manifest.payload["contract"].endswith(".8") else _validate_day_open_agenda_payload
                           if manifest.payload["contract"].endswith((".6", ".7")) else _validate_day_open_reconsider_payload
                           if manifest.payload["contract"].endswith(".5") else _validate_day_open_payload),
            )
        return contract

    def _validate_decision_payload(
        self,
        payload: Mapping[str, object],
        *,
        decision_source_refs: frozenset[str],
        request: _InteriorRoleRequest,
        contract: PurposeDecisionContract,
        response_hash: str,
    ) -> None:
        if "contract" in payload:
            self._raise("payload_contract_reserved", response_hash=response_hash)
        if request.purpose == "external_perception_attention":
            self._validate_external_attention_payload(
                payload,
                decision_source_refs=decision_source_refs,
                request=request,
                response_hash=response_hash,
            )
            return
        offered = self._offered_tokens(request.capability_manifest, contract=contract)
        if contract.selected_token_required:
            token = payload.get("selected_token")
            if not isinstance(token, str) or not token:
                self._raise("selected_token_required", response_hash=response_hash)
            if token not in offered:
                self._raise("selected_token_not_offered", response_hash=response_hash)
        if contract.validator is not None:
            try:
                if request.purpose == "proactive_contact" and request.capability_manifest is not None:
                    _validate_proactive_payload(
                        payload, offered,
                        source_lanes=request.capability_manifest.payload.get(
                            "world_claim_source_lanes", _PROACTIVE_SOURCE_LANES_ABSENT,
                        ),
                    )
                elif request.purpose == "memory_consolidation" and request.capability_manifest is not None:
                    cap = request.capability_manifest.payload
                    from ..memory_consolidation_contract import UNDERSTANDING_CONTRACT
                    if cap.get("understanding_contract") == UNDERSTANDING_CONTRACT:
                        if not isinstance(payload.get("understandings"), list):
                            raise ValueError("understandings must explicitly be updates or []")
                        _validate_periodic_memory(payload, offered,
                            source_tokens=set(cap["understanding_source_tokens"]),
                            predecessor_tokens=set(cap["understanding_tokens"]),
                            readings={**{m["token"]: m["reading"] for m in cap["memories"]},
                                      **{m["token"]: m["reading"] for m in cap["related_memories"]}})
                    else:
                        contract.validator(payload, offered)
                else:
                    contract.validator(payload, offered)
            except StructuredRoleResultError:
                raise
            except (TypeError, ValueError) as exc:
                raise StructuredRoleResultError(
                    "role_result_schema_invalid",
                    detail=str(exc),
                    response_hash=response_hash,
                ) from exc
        if request.purpose == "life_development_choice":
            manifest = request.capability_manifest
            assert manifest is not None
            _normalized_life_development_completion(
                payload,
                manifest=manifest,
                response_hash=response_hash,
            )
        if request.purpose == "outcome_selection":
            manifest = request.capability_manifest
            assert manifest is not None
            direction_value = payload.get("character_life_direction")
            if (
                manifest.payload.get("allow_character_life_direction") is False
                and direction_value is not None
            ):
                raise StructuredRoleResultError(
                    "role_result_schema_invalid",
                    detail="this outcome capability does not authorize a life direction",
                    response_hash=response_hash,
                )
            if direction_value is not None:
                try:
                    from ..character_outcome_contract import CharacterLifeDirectionDraft

                    direction = CharacterLifeDirectionDraft.model_validate(
                        direction_value,
                        strict=True,
                    )
                    raw_coordinates = manifest.payload.get("current_coordinates")
                    coordinates = raw_coordinates if isinstance(raw_coordinates, list) else []
                    for coordinate in coordinates:
                        if not isinstance(coordinate, dict):
                            raise ValueError("current coordinate is malformed")
                        prefixes = coordinate.get("replaces_context_tag_prefixes")
                        coordinate_ref = coordinate.get("coordinate_ref")
                        if not isinstance(prefixes, list) or not isinstance(coordinate_ref, str):
                            raise ValueError("current coordinate is malformed")
                        if (
                            set(prefixes) & set(direction.replaces_context_tag_prefixes)
                            and coordinate_ref != direction.coordinate_ref
                        ):
                            raise ValueError(
                                "character direction must preserve the overlapping coordinate ref"
                            )
                except (TypeError, ValueError) as exc:
                    raise StructuredRoleResultError(
                        "role_result_schema_invalid",
                        detail=str(exc),
                        response_hash=response_hash,
                    ) from exc
            if payload.get("adopt_proposed_life_direction") is True:
                selected_token = payload.get("selected_token")
                raw_candidates = manifest.payload.get("candidates")
                candidates = raw_candidates if isinstance(raw_candidates, list) else []
                selected = next(
                    (
                        item
                        for item in candidates
                        if isinstance(item, dict) and item.get("token") == selected_token
                    ),
                    None,
                )
                proposed = (
                    selected.get("proposed_objective_direction")
                    if isinstance(selected, dict)
                    else None
                )
                if not proposed:
                    raise StructuredRoleResultError(
                        "role_result_schema_invalid",
                        detail=(
                            "adopt_proposed_life_direction is true only for a "
                            "selected candidate that carries proposed_objective_direction"
                        ),
                        response_hash=response_hash,
                    )
        if request.purpose == "media_selection" and payload.get("decision") == "select":
            manifest = request.capability_manifest
            assert manifest is not None
            selected_token = payload.get("selected_token")
            raw_candidates = manifest.payload.get("candidates")
            candidates = raw_candidates if isinstance(raw_candidates, list) else []
            selected = next(
                (
                    item
                    for item in candidates
                    if isinstance(item, dict) and item.get("token") == selected_token
                ),
                None,
            )
            raw_refs = selected.get("source_refs") if isinstance(selected, dict) else []
            candidate_refs = (
                {ref for ref in raw_refs if isinstance(ref, str) and ref}
                if isinstance(raw_refs, list)
                else set()
            )
            if candidate_refs - decision_source_refs:
                self._raise(
                    "media_selection_source_unclosed",
                    response_hash=response_hash,
                )

    @classmethod
    def _validate_external_attention_payload(
        cls,
        payload: Mapping[str, object],
        *,
        decision_source_refs: frozenset[str],
        request: _InteriorRoleRequest,
        response_hash: str,
    ) -> None:
        manifest = request.capability_manifest
        if manifest is None:
            cls._raise("capability_manifest_required", response_hash=response_hash)
        capability = manifest.payload
        raw_candidates = capability.get("candidates")
        deployment_mode = capability.get("deployment_mode")
        if not isinstance(raw_candidates, list) or deployment_mode not in {"shadow", "live"}:
            cls._raise(
                "external_attention_result_shape_invalid",
                response_hash=response_hash,
            )
        candidates: dict[str, Mapping[str, object]] = {}
        for raw_candidate in raw_candidates:
            if not isinstance(raw_candidate, dict):
                cls._raise(
                    "external_attention_result_shape_invalid",
                    response_hash=response_hash,
                )
            candidate_ref = raw_candidate.get("candidate_ref")
            candidate_token = raw_candidate.get("candidate_token")
            if (
                not isinstance(candidate_ref, str)
                or candidate_token != candidate_ref
                or candidate_ref in candidates
            ):
                cls._raise(
                    "external_attention_result_shape_invalid",
                    response_hash=response_hash,
                )
            candidates[candidate_ref] = raw_candidate
        raw_selections = payload.get("selections")
        if not isinstance(raw_selections, list) or set(payload) != {"selections"}:
            cls._raise(
                "external_attention_result_shape_invalid",
                response_hash=response_hash,
            )
        maximum = 12 if deployment_mode == "live" else 16
        if len(raw_selections) > maximum:
            cls._raise(
                "external_attention_result_shape_invalid",
                response_hash=response_hash,
            )
        try:
            selections = tuple(
                _ExternalAttentionSelection.model_validate(item) for item in raw_selections
            )
        except ValidationError as exc:
            raise StructuredRoleResultError(
                "external_attention_result_shape_invalid",
                detail=exc.json(include_url=False),
                response_hash=response_hash,
            ) from exc
        seen: set[str] = set()
        snapshot_refs = set(request.snapshot.source_refs)
        durable = capability.get("durable_snapshots")
        durable_by_revision = (
            {
                item["signal_revision_ref"]: item
                for item in durable
                if isinstance(item, dict) and isinstance(item.get("signal_revision_ref"), str)
            }
            if isinstance(durable, list)
            else {}
        )
        try:
            raw_materials = json.loads(getattr(request.snapshot, "materials_json", "") or "{}")
        except (TypeError, ValueError):
            raw_materials = {}
        prior_perceptions = (
            raw_materials.get("perception") if isinstance(raw_materials, Mapping) else None
        )
        prior_summaries = (
            {
                item["subjective_summary"].strip()
                for item in prior_perceptions
                if isinstance(item, Mapping)
                and isinstance(item.get("subjective_summary"), str)
                and item["subjective_summary"].strip()
            }
            if isinstance(prior_perceptions, list)
            else set()
        )
        for selection in selections:
            if selection.candidate_ref in seen:
                cls._raise(
                    "external_attention_duplicate_candidate",
                    response_hash=response_hash,
                )
            seen.add(selection.candidate_ref)
            if selection.subjective_summary.strip() in prior_summaries:
                raise StructuredRoleResultError(
                    "role_result_schema_invalid",
                    detail=(
                        "subjective_summary copied an earlier perception from inner_life_snapshot; "
                        "describe the newly selected candidate in capability_manifest.payload.candidates instead"
                    ),
                    response_hash=response_hash,
                )
            candidate = candidates.get(selection.candidate_ref)
            if candidate is None:
                cls._raise(
                    "external_attention_candidate_not_offered",
                    response_hash=response_hash,
                )
            raw_revisions = candidate.get("exact_signal_revisions")
            offered_revisions = (
                {item for item in raw_revisions if isinstance(item, str)}
                if isinstance(raw_revisions, list)
                else set()
            )
            if not set(selection.exact_signal_revision_refs) <= offered_revisions:
                cls._raise(
                    "external_attention_revision_not_offered",
                    response_hash=response_hash,
                )
            raw_channels = candidate.get("accessible_channels")
            channels = (
                {
                    item["channel_ref"]: item
                    for item in raw_channels
                    if isinstance(item, dict) and isinstance(item.get("channel_ref"), str)
                }
                if isinstance(raw_channels, list)
                else {}
            )
            channel = channels.get(selection.selected_channel_ref)
            if channel is None:
                cls._raise(
                    "external_attention_channel_not_offered",
                    response_hash=response_hash,
                )
            raw_material = candidate.get("model_visible_material")
            revision_sources = (
                {
                    item["signal_revision_ref"]: item.get("source_id")
                    for item in raw_material
                    if isinstance(item, dict) and isinstance(item.get("signal_revision_ref"), str)
                }
                if isinstance(raw_material, list)
                else {}
            )
            accessible_sources = channel.get("accessible_source_ids")
            accessible = (
                {item for item in accessible_sources if isinstance(item, str)}
                if isinstance(accessible_sources, list)
                else set()
            )
            if any(
                revision_sources.get(revision) not in accessible
                for revision in selection.exact_signal_revision_refs
            ):
                cls._raise(
                    "external_attention_channel_inaccessible",
                    response_hash=response_hash,
                )
            if set(selection.attended_context_refs) - snapshot_refs:
                cls._raise(
                    "external_attention_context_unpinned",
                    response_hash=response_hash,
                )
            channel_proofs = channel.get("evidence_refs")
            # Signal revisions are sidecar capability tokens, already closed
            # over the manifest payload and (live) durable snapshot hashes.
            # Only committed channel authority refs belong in source_refs.
            required_sources: set[str] = set()
            if isinstance(channel_proofs, list):
                required_sources.update(item for item in channel_proofs if isinstance(item, str))
            if not required_sources <= decision_source_refs:
                cls._raise(
                    "external_attention_decision_source_unclosed",
                    response_hash=response_hash,
                )
            if deployment_mode == "live":
                if selection.privacy_class is None:
                    cls._raise(
                        "external_attention_privacy_invalid",
                        response_hash=response_hash,
                    )
                authored = selection.subjective_summary + "\n" + selection.epistemic_notes
                for revision_ref in selection.exact_signal_revision_refs:
                    source = durable_by_revision.get(revision_ref)
                    if (
                        source is not None
                        and source.get("may_quote") is False
                        and (
                            cls._reproduces_non_quotable_text(
                                output=authored,
                                source=source.get("headline"),
                                minimum_protected_characters=4,
                            )
                            or cls._reproduces_non_quotable_text(
                                output=authored,
                                source=source.get("licensed_summary"),
                            )
                        )
                    ):
                        cls._raise(
                            "external_attention_nonquotable_reproduced",
                            response_hash=response_hash,
                        )
            elif selection.privacy_class is not None:
                cls._raise(
                    "external_attention_privacy_invalid",
                    response_hash=response_hash,
                )

    @staticmethod
    def _reproduces_non_quotable_text(
        *,
        output: str,
        source: object,
        minimum_protected_characters: int = 8,
    ) -> bool:
        if not isinstance(source, str):
            return False
        normalize = lambda value: "".join(  # noqa: E731 - tiny local transform
            unicodedata.normalize("NFKC", value).casefold().split()
        )
        protected = normalize(source)
        candidate = normalize(output)
        if len(protected) < minimum_protected_characters:
            return False
        if len(protected) <= 32:
            return protected in candidate
        return any(
            protected[index : index + 32] in candidate for index in range(len(protected) - 31)
        )

    @staticmethod
    def _offered_tokens(
        manifest: _InteriorCapabilityManifest | None,
        *,
        contract: PurposeDecisionContract,
    ) -> frozenset[str]:
        if not contract.offered_token_fields:
            return frozenset()
        if manifest is None:
            raise StructuredRoleResultError(
                "capability_manifest_required",
                detail=_FAILURE_DETAILS["capability_manifest_required"],
            )
        tokens: set[str] = set()
        payload = manifest.payload
        for field in contract.offered_token_fields:
            raw_items = payload.get(field)
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if isinstance(item, str) and item:
                    tokens.add(item)
                elif isinstance(item, dict):
                    for key in (
                        "token",
                        "candidate_token",
                        "signal_token",
                        "attachment_token",
                        "attachment_ref",
                    ):
                        value = item.get(key)
                        if isinstance(value, str) and value:
                            tokens.add(value)
                            break
        if not tokens:
            raise StructuredRoleResultError(
                "capability_manifest_missing_offered_tokens",
                detail=_FAILURE_DETAILS["capability_manifest_missing_offered_tokens"],
            )
        return frozenset(tokens)

    @staticmethod
    def _normalize_decision(
        decision: _WireDecision | None,
        *,
        request: _InteriorRoleRequest,
        contract: PurposeDecisionContract,
    ) -> dict[str, object] | None:
        if decision is None:
            return None
        normalized: dict[str, object] = {
            "contract": "character-interior-purpose-decision.1",
            "purpose": request.purpose,
            "source_refs": list(decision.source_refs),
        }
        if request.capability_manifest is not None:
            normalized.update(
                {
                    "capability_ref": request.capability_manifest.capability_ref,
                    "capability_payload_hash": request.capability_manifest.payload_hash,
                }
            )
        payload = dict(decision.payload)
        if _is_explicit_media_no_op(payload):
            payload.pop("selected_token", None)
        if request.purpose == "life_development_choice":
            manifest = request.capability_manifest
            assert manifest is not None
            payload["completion"] = _normalized_life_development_completion(
                payload,
                manifest=manifest,
            )
        normalized["payload"] = {"contract": contract.payload_contract, **payload}
        return normalized

    @staticmethod
    def _normalize_proposals(
        proposals: Sequence[Mapping[str, object]],
        *,
        request: _InteriorRoleRequest,
        contract: PurposeDecisionContract,
    ) -> tuple[dict[str, object], ...]:
        if not proposals or contract.proposal_type is None:
            return tuple(dict(item) for item in proposals)
        manifest = request.capability_manifest
        assert manifest is not None
        if contract.proposal_type == "world_stimulus_appraisal_result":
            world_result = _WorldStimulusAppraisalResult.model_validate(proposals[0])
            value = world_result.model_dump(mode="json")
            value.pop("proposal_type")
            return (
                {
                    "contract": "character-interior-typed-proposal.1",
                    "proposal_type": contract.proposal_type,
                    "purpose": request.purpose,
                    "source_refs": list(manifest.source_refs),
                    "capability_ref": manifest.capability_ref,
                    "capability_payload_hash": manifest.payload_hash,
                    "payload": {
                        "contract": contract.proposal_payload_contract,
                        **value,
                    },
                },
            )
        proposal = _PrivateImpressionProposal.model_validate(proposals[0])
        # The private-impression capability hands the model short
        # position-stable tokens; map them back to the real source refs before
        # the proposal payload is persisted, so authority validation and the
        # reducer only ever see canonical source identities.
        manifest_raw_token_map = manifest.payload.get("token_map")
        if isinstance(manifest_raw_token_map, dict) and (
            any(item in manifest_raw_token_map for item in proposal.source_refs)
            or any(item in manifest_raw_token_map for item in proposal.predecessor_refs)
        ):
            proposal = proposal.model_copy(
                update={
                    "source_refs": [
                        manifest_raw_token_map.get(item, item) for item in proposal.source_refs
                    ],
                    "predecessor_refs": [
                        manifest_raw_token_map.get(item, item) for item in proposal.predecessor_refs
                    ],
                }
            )
        value = proposal.model_dump(mode="json")
        value.pop("proposal_type")
        return (
            {
                "contract": "character-interior-typed-proposal.1",
                "proposal_type": contract.proposal_type,
                "purpose": request.purpose,
                # Trusted evidence closure belongs to the capability, never
                # to model-selected reflection tokens inside the payload.
                "source_refs": list(manifest.source_refs),
                "capability_ref": manifest.capability_ref,
                "capability_payload_hash": manifest.payload_hash,
                "payload": {
                    "contract": contract.proposal_payload_contract,
                    **value,
                },
            },
        )

    @staticmethod
    def _capability_view(
        manifest: _InteriorCapabilityManifest | None,
        *, compact_memory: bool = False,
    ) -> dict[str, object]:
        if manifest is None:
            return {"availability": "unavailable"}
        payload = dict(manifest.payload)
        if manifest.capability_kind == "private_impression_reflection":
            payload = _private_impression_provider_payload(payload)
        elif manifest.capability_kind == "memory_consolidation":
            from ..memory_consolidation_contract import HOST_SOURCE_BINDING, UNDERSTANDING_CONTRACT

            payload = _memory_consolidation_provider_payload(payload, compact=(
                compact_memory and payload.get("source_binding") == HOST_SOURCE_BINDING
                and payload.get("understanding_contract") == UNDERSTANDING_CONTRACT
            ))
        return {
            "availability": "available",
            "capability_ref": manifest.capability_ref,
            "capability_kind": manifest.capability_kind,
            "payload_hash": manifest.payload_hash,
            "payload": payload,
            "source_refs": list(manifest.source_refs),
        }

    @staticmethod
    def _contract_view(
        contract: PurposeDecisionContract,
        *,
        capability_manifest: _InteriorCapabilityManifest | None,
    ) -> dict[str, object]:
        view: dict[str, object] = {
            "purpose": contract.purpose,
            "payload_contract": contract.payload_contract,
        }
        if contract.capability_kind is not None:
            view["capability_kind"] = contract.capability_kind
        if contract.selected_token_required:
            view["selected_token"] = "one token from capability_manifest.payload"
        view["proposals"] = "allowed" if contract.proposals_allowed else "must_be_empty"
        if contract.purpose == "media_selection":
            view["payload_schema"] = {
                "decision": "select|no_op",
                "selected_token": "required only for select; one offered token",
            }
        if contract.purpose == "qq_attachment_perception":
            view["payload_schema"] = {
                "decision": "select|no_op",
                "selected_token": "required only for select; one offered attachment token",
            }
        if contract.purpose == "external_perception_attention":
            view["payload_schema"] = {
                "selections": [
                    {
                        "candidate_ref": "one offered candidate_token",
                        "exact_signal_revision_refs": ["one or more revisions from that candidate"],
                        "selected_channel_ref": "one channel from that candidate",
                        "subjective_summary": "the character's own natural first-person reaction",
                        "epistemic_notes": "optional brief note if source is dubious; empty string when none",
                        "attended_context_refs": ["optional canonical inner-life source refs"],
                        "privacy_class": "live only: public|shareable|personal|private|withhold",
                    }
                ],
                "selection_count": "zero_or_many",
            }
        if contract.purpose == "proactive_contact":
            view["payload_schema"] = {
                "timing_choice": "now|later|silent",
                "beats": (
                    "zero only for silent; otherwise one or more offered expression beats "
                    "(to send a QQ voice note when not in a quiet setting like class/library/sleeping dorm, "
                    "use modality='text' with text='[CQ:voice]（语气提示）台词')"
                ),
                "delay_seconds": "later only",
                "expires_after_seconds": "later only",
                "stance": "free text",
                "brief_rationale": "short free text",
                "impulse_summary": "free text",
                "confidence": "integer 0..10000",
                "world_claims": [
                    {
                        "claim_text": "one concrete claim",
                        "scope": (
                            "current_world|past_world|counterpart_history|"
                            "shared_history|stable_identity|subjective_or_hypothetical"
                        ),
                        "source_refs": ["one supplied matching pinned ref"],
                    }
                ],
                "world_claims_rule": (
                    "Use objects, never strings. Only factual claims need entries; "
                    "feelings and hypothetical impulses may omit the claim (or use "
                    "scope=subjective_or_hypothetical with source_refs: []). "
                    "Every grounded claim cites at least one matching "
                    "supplied source ref whose world_claim_scopes in citeable_sources.items "
                    "contains that exact scope (never cite a source with world_claim_scopes: [] "
                    "in world_claims; put those only in attended_source_refs); "
                    "use [] when the beats contain no factual claim. "
                    "A dialogue beat is not current_world."
                ),
                "private_state_rule": (
                    "私人状态只写在外层 summary / attended_source_refs；"
                    "payload 里禁止 private_turn_state（这一点和 inbound 相反）。"
                    "attended_source_refs 只写 citeable_sources 的 id 或原样抄 ref。"
                ),
                "author_notes": (
                    "没有可核对的世界事实时 world_claims 写 []。"
                    "对话 beat 不是 current_world；current_world 只能引用当前生活/世界来源，"
                    "不能引用她自己上一句对话。"
                    "复述自己已经说过的话时，用 shared_history，并引用那条 companion 对话的 "
                    "item_ref / source_refs；引用他刚说的确认用 counterpart_history。"
                ),
                "waiting_for": "optional short sentence; compiles a hope only with wait",
                "wait": "optional seconds 5..86400; only with waiting_for",
                "about_us": "optional short reading of us; with why_us",
                "why_us": "optional short reason; with about_us",
                "us_deltas": (
                    "optional signed six-axis object; only with about_us and why_us. "
                    "half-written pairs fail visibly."
                ),
                "we_are": "optional stage; only with calling_it and said_as",
                "calling_it": "optional name for the stage; only with we_are and said_as",
                "said_as": "optional exact spoken line; only with we_are and calling_it",
                "keep_impression": "optional bool; true requires non-empty stuck_with_me and this turn's sourced appraisal, never appraise=false",
                "stuck_with_me": "exact private understanding she chooses to retain with keep_impression=true",
                "noticed": "optional turn-local subjective attention; audit only, no World fact or completed Action",
                "declared_display": (
                    "optional sexual_suggestive|explicit_adult|withdraw; omit to leave the last declaration"
                ),
                "response_expectation": (
                    "若写：hoped_response、pressure_bp、importance_bp、wait_seconds、"
                    "expires_after_seconds。也可改写 waiting_for + wait，规则与入站相同。"
                    "pressure_bp 与 importance_bp 是 0 到 10000 的基点（basis points），"
                    "不是 0 到 100 的百分制：5000=一半，3000=三成，30 不是百分之三十。"
                ),
                "revisit": (
                    "可选对象：thought（1..160字）、wait_seconds（5..86400）、expires_after_seconds（大于wait_seconds），"
                    "用于安排指定秒数后再次唤醒自己；不需要时写 null。"
                ),
                "media_request": (
                    "可选 none|consider_available_candidate（仅当 timing_choice='now' 且想发图时写 consider_available_candidate；默认写 none）。"
                ),
                "media_source_refs": (
                    "配合 media_request='consider_available_candidate' 使用的可选事件来源列表（已有现成候选时可写 []；media_request='none' 时必须写 []）。"
                ),
                "appraisal_draft": (
                    "可选对象：本轮产生需要记录或更新的持续情绪时可写 appraisal_draft（appraise=true 并设 affect=open/update/supersede 及 components）；无变化时写 null 或省略。"
                ),
            }
        if contract.purpose == "expression_reconsideration":
            view["payload_schema"] = {
                "disposition": "continue|cancel|defer|merge|supersede|new_beat"
            }
        if contract.purpose == "private_impression_reflection":
            existing_impression_tokens = (
                capability_manifest.payload.get("existing_impression_short_tokens")
                if capability_manifest is not None
                else None
            )
            replacement_available = bool(existing_impression_tokens)
            view["status_schema"] = {
                "no_change": (
                    "proposals must be []; character-chosen omit—host never forces retain"
                ),
                "transition": "exactly one private_impression_transition proposal",
            }
            decision_meanings = {
                "no_change": (
                    "这次先不动印象：不开新的，也不搁下已有的。省略也完全正常。"
                ),
                "retain": (
                    "记下一条新的、仍可改的私人印象，用第一人称写出你此刻的想法。"
                ),
            }
            if replacement_available:
                decision_meanings.update(
                    {
                        "consolidate": (
                            "把关于同一件事或同一个人的几条零散印象合成一条当前想法。"
                        ),
                        "supersede": (
                            "用一条新的真实想法替代旧印象。"
                        ),
                        "release": (
                            "这件事已经过去或说开了，可以搁下了。选或不选都正常。"
                        ),
                    }
                )
            view["proposal_schema"] = {
                "proposal_type": "private_impression_transition",
                "decision": (
                    "retain|consolidate|supersede|release"
                    if replacement_available
                    else "retain"
                ),
                "decision_meanings": decision_meanings,
                "predecessor_refs": (
                    "selected existing-impression short tokens from "
                    "capability_manifest.payload.existing_impression_short_tokens"
                    if replacement_available
                    else "must be []"
                ),
                "source_refs": (
                    "selected offered short tokens from "
                    "capability_manifest.payload.short_tokens; at least one "
                    "anchor_short_tokens entry must be included"
                ),
                "cross_field_rules": (
                    "predecessor_refs are existing-impression short tokens, every "
                    "predecessor must also be listed in source_refs, retain has no "
                    "predecessors, consolidate/supersede has at least one, and "
                    "release puts down exactly one existing impression"
                    if replacement_available
                    else (
                        "retain is the only installed transition when no prior "
                        "impression exists; status=no_change remains available "
                        "and equally valid when she chooses not to keep one"
                    )
                ),
                "reflection_summary": (
                    "free tentative private reading; not a completed send or reply "
                    "through the user channel"
                ),
                "confidence_bp": "integer 0..10000",
                "expiry_condition": "one offered lifecycle condition",
            }
        if contract.purpose == "world_stimulus_appraisal":
            view["status_schema"] = {
                "no_change": (
                    "one result proposal whose decision is no_change and whose "
                    "aspiration_transition, experience_transition and life_intent are null"
                ),
                "transition": (
                    "one result proposal whose decision is activate or whose optional "
                    "aspiration_transition, experience_transition or life_intent is present"
                ),
            }
            view["proposal_example_activate"] = {
                "proposal_type": "world_stimulus_appraisal_result",
                "decision": "activate",
                "brief_rationale": "<your private reason in your own voice>",
                "behavior_tendency": "<your actual behavioral tendency>",
                "stance": "<your genuine inner attitude or feeling>",
                "display_strategy": "<how or whether you show it>",
                "confidence": 7000,
                "meaning_candidates": [
                    {"meaning": "<first personal reading of what happened>", "confidence": 6000},
                    {
                        "meaning": "<second possible reading if any>",
                        "confidence": 4000,
                    },
                ],
                "attribution": "situation",
                "severity": 4000,
                "expiry": "2026-08-09T12:00:00Z",
                "affect_transition": {
                    "operation": "open",
                    "component_targets": [
                        {
                            "dimension": "<sadness|hurt|loneliness|anxiety|anger|resentment|warmth|joy>",
                            "target_intensity_bp": 4500,
                        }
                    ],
                },
                "relationship_signal": None,
                "aspiration_transition": None,
                "experience_transition": None,
            }
            view["proposal_example_no_change"] = {
                "proposal_type": "world_stimulus_appraisal_result",
                "decision": "no_change",
                "brief_rationale": "This happened but it does not change how I feel.",
                "behavior_tendency": "Continue as before.",
                "stance": "Unchanged.",
                "display_strategy": "No outward change.",
                "confidence": 5000,
                "meaning_candidates": None,
                "attribution": None,
                "severity": None,
                "expiry": None,
                "affect_transition": None,
                "relationship_signal": None,
                "aspiration_transition": None,
                "experience_transition": None,
            }
            view["proposal_schema"] = {
                "proposal_type": "world_stimulus_appraisal_result",
                "decision": (
                    "no_change means the character has no appraisal at all: "
                    "meaning_candidates, attribution, severity, expiry, "
                    "affect_transition and relationship_signal must be null. "
                    "Independent aspiration_transition, experience_transition or life_intent "
                    "may still be chosen when their capabilities allow them. Activate "
                    "requires a complete appraisal. Choose the appraisal independently "
                    "of any future activity intention"
                ),
                "brief_rationale": "the character's short private reason (required)",
                "behavior_tendency": "free text (required, even for no_change)",
                "stance": "free text (required, even for no_change)",
                "display_strategy": "free text (required, even for no_change)",
                "confidence": "integer 0..10000",
                "meaning_candidates": (
                    'activate only: one or more objects, each with "meaning": a short '
                    "free-text tentative interpretation grounded in the supplied source "
                    'context, and "confidence": an integer 0..10000 private weight; '
                    "meanings must be unique and are not enum labels or facts about "
                    "another person"
                ),
                "attribution": "activate only: user|companion|npc|situation|third_party|unknown",
                "severity": "activate only: integer 0..10000",
                "expiry": "activate only: ISO-8601 datetime string or null",
                "affect_transition": {
                    "availability": "activate only; null means no Affect lifecycle change",
                    "operation": "open|update|resolve|supersede",
                    "open": (
                        "component_targets with unique dimension and target_intensity_bp; "
                        "dimension must be one exact token of hurt, anger, sadness, "
                        "loneliness, anxiety, resentment, warmth, joy; targets must "
                        "satisfy affect_target_lower_bounds"
                    ),
                    "update": (
                        "one episode_id from active_affect_heads plus component_targets "
                        "that name exact offered component_id, dimension and intensity"
                    ),
                    "resolve": (
                        "one episode_id from active_affect_heads and a free-text resolution_summary"
                    ),
                    "supersede": (
                        "one episode_id from active_affect_heads plus new successor "
                        "component_targets"
                    ),
                },
                "relationship_signal": (
                    "activate only: optional source-bound relationship signal for one "
                    "supplied relationship_subject_ref; when relationship_subject_refs is empty, leave this null; null means no relationship change"
                ),
                "aspiration_transition": {
                    "availability": "optional for either appraisal decision",
                    "operation": "plant|reinforce|revise|abandon",
                    "aspiration_id": "null for plant; otherwise one supplied active id",
                    "text": "free text for plant/revise; otherwise null",
                    "privacy_class": "plant/revise only: public|shareable|personal|private|withhold",
                    "tension_summary": "optional free internal conflict, not a category",
                    "tension_source_refs": "sources supporting that tension or []",
                    "source_refs": "current stimulus plus any selected aspiration authority",
                    "reason_summary": "short free private reason",
                },
                "experience_transition": {
                    "availability": (
                        "optional one-of goal|thread|commitment|memory_candidate; null is "
                        "always allowed; select only exact heads/sources and operations "
                        "listed by capability_manifest.payload.experience_transitions"
                    ),
                    "goal": (
                        "pause|resume|abandon an offered head; creating a Goal is unavailable "
                        "because no Goal content authority is installed"
                    ),
                    "thread": (
                        "open a source-bound private unfinished thought from the current "
                        "experience, or update|resolve|cancel an offered head. Choose "
                        "thread_kind=reply_reconsideration only when you genuinely want to "
                        "revisit possible future contact; choose its due_at, expires_at and "
                        "importance yourself. Opening it records an inner intention only: it "
                        "does not send or schedule a message. At due time the proactive lane "
                        "must ask you again to choose now, later, or silent"
                    ),
                    "commitment": (
                        "open only against an offered open Thread fulfillment contract, or "
                        "release an offered commitment head"
                    ),
                    "memory_candidate": (
                        "retain only the offered exact Fact, Experience, or terminal Thread source"
                    ),
                    "source_refs": "the exact closure required by the selected capability",
                    "reason_summary": "the character's free private reason, not a system motive code",
                },
                "life_intent": (
                    "null or one future self_directed intention, only when the "
                    "world_life_intent capability offers its exact source_event_ref. "
                    "Choose intention, start_after_seconds, duration_seconds and importance_bp "
                    "yourself. This requests a Plan; it does not claim execution, control "
                    "a place or another person, or send anything. No appraisal is required"
                ),
            }
            if capability_manifest is not None and capability_manifest.payload.get('world_plan_management') is not None:
                view['proposal_schema']['plan_response'] = (
                    'null or {operation:continue|revise|end, plan_id:<exact offered own Plan>, '
                    'source_event_ref:<offered settlement>}. Continue keeps its existing intention and '
                    'lifecycle unchanged; it does not execute it again. End puts down the intention '
                    '(abandon), not a claim of successful completion. Revise additionally supplies '
                    'replacement:<LifeIntentDraft>: one linked replacement version ends its predecessor. '
                    'If you are continuing, changing or putting down an offered intention, refer to '
                    'that Plan here rather than recreating the same intention as a new Plan. '
                    'Choose these yourself. life_intent creates a separate new Plan, never an update; '
                    'do not provide it alongside plan_response. Leaving both null is valid.'
                )
                view['status_schema']['no_change'] += ' and plan_response is null'
                view['status_schema']['transition'] += ' or plan_response is present'
            response_capability = (
                capability_manifest.payload.get("world_life_response")
                if capability_manifest is not None else None
            )
            if response_capability is not None:
                view["status_schema"] = {
                    "transition": "one result proposal including all required life_responses; "
                    "decision=no_change still means no Appraisal is chosen"
                }
                view["proposal_schema"]["life_responses"] = {
                    "contract": "world-life-response-array.1",
                    "coverage": "one entry per exact world_life_response.source_event_refs; "
                    "missing entries, omitted response_text and duplicate sources are invalid",
                    "source_event_ref": "one exact offered settlement source",
                    "response_text": "required text or explicit null; your freely authored "
                    "private response to this consequence, not an additional World result. "
                    "Null means you choose no response text for this source. It neither "
                    "requires Appraisal nor future action; those remain independent choices",
                }
                for example in ("proposal_example_activate", "proposal_example_no_change"):
                    view[example]["life_responses"] = [
                        {"source_event_ref": ref, "response_text": None}
                        for ref in response_capability["source_event_refs"]
                    ]
        if contract.purpose == "life_development_choice":
            view["payload_schema"] = {"completion": "one complete JSON object"}
        if contract.purpose == "activity_lifecycle_choice":
            view["payload_schema"] = {
                "decision": "select|no_op",
                "selected_token": "select only: one offered activity token",
                "noticed": "optional turn-local subjective attention; audit only, no World fact or completed Action",
                'user_channel_completion': 'with non-null noticed: the literal JSON string "none" is required; otherwise omit or null both fields',
            }
            if contract.payload_contract in {"character-interior-activity-lifecycle-choice.2", "character-interior-activity-lifecycle-choice.3"}:
                view["payload_schema"] = {
                    "decision": "self_directed_intent|no_op",
                    "life_intent": "only with self_directed_intent: execution_scope=self_directed, "
                    "your own intention, start_after_seconds, duration_seconds and importance_bp. "
                    "start_after_seconds is 0..86400 from the current Clock, so choosing "
                    "a later activity does not require being ready to start now; duration_seconds "
                    "is 60..21600. This opening offers a future intention independent of "
                    "the last conversation or activity. "
                    "This is a future private Plan, not execution, place control, other people's "
                    "participation or a World result. no_op has no other fields and is a valid choice",
                }
                if contract.payload_contract.endswith(".3"):
                    view["payload_schema"]["decision"] = "self_directed_intent|no_op|reconsider"
                    view["payload_schema"]["reconsider_after_seconds"] = (
                        "reconsider only: your chosen integer 60..86400 seconds until another "
                        "planning opportunity. No life_intent in this branch. It creates no "
                        "activity or message; you choose again using the then-current context"
                    )
            if contract.payload_contract in {"character-interior-activity-lifecycle-choice.4", "character-interior-activity-lifecycle-choice.5", "character-interior-activity-lifecycle-choice.6"}:
                view["payload_schema"] = {
                    "decision": "self_directed_agenda|no_op|reconsider",
                    "life_intents": "self_directed_agenda only: 1..8 complete LifeIntentDraft objects. "
                    "Each has execution_scope=self_directed, intention (1..480 chars), "
                    "start_after_seconds (0..86400 from this Clock), duration_seconds (60..21600), "
                    "importance_bp (0..10000). Choose a coherent rolling portion of your own life; "
                    "include useful concrete intended steps in each intention. Choose the number "
                    "and rhythm yourself; gaps and changes of mind are allowed. Read existing plans "
                    "before adding more. Routine is only habit background: it does not establish "
                    "a real class, appointment, place availability or another person's participation. "
                    "These are future intentions, never completed actions, outcomes or emotions.",
                    "reconsider_after_seconds": "reconsider only: integer 60..86400; no life_intents",
                    "no_op": "decision only; creates no activity or message",
                }
                if contract.payload_contract.endswith(".5"):
                    view["payload_schema"]["decision"] += "|continue_existing"
                    view["payload_schema"]["plan_ids"] = (
                        "continue_existing only: 1..8 distinct exact IDs from existing_plans. "
                        "Retain their accepted intentions, time windows and lifecycle unchanged; "
                        "unselected Plans also remain unchanged. Do not add life_intents or imply execution, "
                        "rescheduling or ending a Plan. A completed activity does not "
                        "mean your remaining agenda needs to be recreated. A separate new intention "
                        "is still your choice. no_op also leaves all existing Plans unchanged."
                    )
                if contract.payload_contract.endswith(".6"):
                    view["payload_schema"]["decision"] += "|manage_plan|continue_existing"
                    view["payload_schema"]["plan_ids"]="continue_existing only: 1..8 distinct exact offered Plan IDs; acknowledge all without mutation."
                    view["payload_schema"]["plan_response"] = (
                        "manage_plan only: {operation:continue|revise|end,plan_id:<exact offered own ID>,"
                        "replacement:<LifeIntentDraft, revise only>}. Continue only acknowledges the Plan; "
                        "revise abandons its predecessor and links a replacement; end abandons, not completes. "
                        "Other Plans remain unchanged. Separate new intentions stay your choice."
                    )
        if contract.purpose == "outcome_selection":
            view["payload_schema"] = {
                "selected_token": "exactly one offered outcome token",
                "adopt_proposed_life_direction": (
                    "true only when the selected candidate carries "
                    "proposed_objective_direction and that exact candidate's "
                    "objective life consequence becomes true; otherwise false"
                ),
                "character_life_direction": (
                    "null or one freely authored, structurally closed subjective direction"
                ),
            }
        if contract.purpose in {
            "fact_memory_retention",
            "experience_memory_retention",
        }:
            view["payload_schema"] = {
                "retain": "boolean",
                "when_retain_true": [
                    "cue_kind",
                    "retention_rationales",
                    "salience",
                ],
                "when_retain_false": "no other fields",
            }
        if contract.purpose == "memory_withdrawal_review":
            view["payload_schema"] = {"selected_token": "one offered retain|forget|revise token"}
        return view

    @staticmethod
    def _model_call_id(
        *,
        request: _InteriorRoleRequest,
        request_hash: str,
    ) -> str:
        identity = {
            "inner_turn_id": request.inner_turn_id,
            "phase": request.phase,
            "purpose": request.purpose,
            "attempt_ordinal": request.correction_ordinal,
            "request_hash": request_hash,
        }
        return "model-call:character-interior:" + _hash_text(_canonical(identity))

    @staticmethod
    def _raise(
        code: str,
        *,
        response_hash: str,
        rejected_raw: str | None = None,
        detail: str | None = None,
    ) -> None:
        raise StructuredRoleResultError(
            code,
            detail=detail or _FAILURE_DETAILS[code],
            response_hash=response_hash,
            rejected_raw=rejected_raw,
        )


__all__ = [
    "PurposeDecisionContract",
    "StructuredCharacterRoleFaculty",
    "StructuredRoleResultError",
]
