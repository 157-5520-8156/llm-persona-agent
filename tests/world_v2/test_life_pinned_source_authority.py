"""Pinned source lookup must never promote prose or candidate memories."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.context_capsule import (
    ResolvedSourceBinding,
    source_bindings_hash,
)
from companion_daemon.world_v2.life_development_source_closure import (
    life_development_source_closure_messages,
    pinned_context_grounding_refs,
    resolve_cited_pinned_material,
)
from test_world_consequence_source_closure import (
    _existing_world_material_for_ref,
    _legacy_material,
)


REF = "biography:trusted"


def _item(*, ref=REF, value=None):
    source_ref = (
        (value or {})
        .get("source_bindings", [{}])[0]
        .get("authority_event_ref", "event:timeline-configured")
    )
    binding = ResolvedSourceBinding(
        source_kind="projection_snapshot",
        authority_type="BiographicalWorldContextItem",
        ref=source_ref,
        source_world_revision=1,
        immutable_hash="a" * 64,
    )
    value = value or {
        "context_kind": "biographical_context",
        "biography_id": ref,
        "reviewed_timeline_ref": "reviewed-biography:timeline",
        "timeline_source_event_ref": "event:timeline-configured",
        "logical_at": "2026-07-29T10:00:00Z",
        "age": 21,
        "season": "summer",
        "calendar_context_tags": [],
        "current_residence_context_tags": [],
        "active_life_arcs": [],
        "source_bindings": [
            {
                "authority_event_ref": "event:timeline-configured",
                "authority_world_revision": 1,
                "authority_payload_hash": "a" * 64,
            }
        ],
    }
    return {
        "item_ref": ref,
        "privacy_class": "personal",
        "source_bindings": [binding.model_dump(mode="json")],
        "source_hash": source_bindings_hash((binding,)),
        "value_hash": hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "value": value,
    }


def _context(item, *, name="world_life", availability="available"):
    return {"slices": {name: {"availability": availability, "items": [item]}}}


@pytest.mark.parametrize("name", ["active_memory_candidates", "world_life"])
def test_prose_containing_ref_is_not_the_referenced_source(name):
    manifest, _ = _legacy_material()
    item = _item(ref="candidate:unverified", value={"text": "I mentioned " + REF})
    assert (
        resolve_cited_pinned_material(context=_context(item, name=name), manifest=manifest, ref=REF)
        is None
    )


def test_even_exact_memory_candidate_identity_is_not_existing_fact_authority():
    manifest, _ = _legacy_material()
    assert (
        resolve_cited_pinned_material(
            context=_context(_item(), name="active_memory_candidates"),
            manifest=manifest,
            ref=REF,
        )
        is None
    )


@pytest.mark.parametrize("damage", ["unavailable", "value_hash", "source_hash", "no_binding"])
def test_unavailable_or_unbound_material_cannot_be_promoted(damage):
    manifest, _ = _legacy_material()
    item = deepcopy(_item())
    if damage.endswith("hash"):
        item[damage] = "b" * 64
    elif damage == "no_binding":
        item.pop("source_bindings")
    assert (
        resolve_cited_pinned_material(
            context=_context(
                item, availability="unavailable" if damage == "unavailable" else "available"
            ),
            manifest=manifest,
            ref=REF,
        )
        is None
    )


def test_exact_source_bound_material_preserves_private_evidence():
    manifest, _ = _legacy_material()
    item = _item()
    material = resolve_cited_pinned_material(context=_context(item), manifest=manifest, ref=REF)
    assert material is not None
    assert material["materials"] == [{"slice": "world_life", "item": item}]


@pytest.mark.parametrize("ref", [REF, "reviewed-biography:timeline", "event:timeline-configured"])
def test_typed_biography_and_timeline_are_citable_in_the_exact_review_packet(ref):
    manifest, draft = _legacy_material()
    manifest, draft = _existing_world_material_for_ref(manifest, draft, ref)
    manifest = manifest.model_copy(update={"pinned_source_materials_version": "2"})
    item = _item()
    context = _context(item)
    assert ref in pinned_context_grounding_refs(context)
    material = resolve_cited_pinned_material(context=context, manifest=manifest, ref=ref)
    assert material is not None
    messages = life_development_source_closure_messages(
        context=context,
        manifest=manifest,
        draft=draft,
        cited_events=(),
        cited_pinned_materials=(material,),
    )
    assert json.loads(messages[1]["content"])["pinned_source_evidence"][
        "cited_pinned_materials"
    ] == [material]
    forged = deepcopy(material)
    forged["materials"][0]["item"]["value"]["age"] = 41
    with pytest.raises(ValueError, match="source-bound authority"):
        life_development_source_closure_messages(
            context=context,
            manifest=manifest,
            draft=draft,
            cited_events=(),
            cited_pinned_materials=(forged,),
        )


@pytest.mark.parametrize(
    "damage", ["identity", "unknown_type", "inner_hash", "inner_source", "privacy"]
)
def test_hash_bound_but_mismatched_typed_material_is_not_evidence(damage):
    manifest, _ = _legacy_material()
    value = _item()["value"]
    if damage == "identity":
        value["biography_id"] = "biography:other"
    elif damage == "unknown_type":
        value["context_kind"] = "made-up"
    elif damage == "inner_hash":
        value["source_bindings"][0]["authority_payload_hash"] = "b" * 64
    elif damage == "inner_source":
        value["source_bindings"][0]["authority_event_ref"] = "event:other"
    else:
        value["privacy_class"] = "private"
    item = _item(value=value)
    assert resolve_cited_pinned_material(context=_context(item), manifest=manifest, ref=REF) is None


@pytest.mark.parametrize("field", ["authority_type", "source_ref", "ref", "reviewed_timeline_ref"])
def test_non_identity_fields_do_not_gain_source_authority(field):
    manifest, _ = _legacy_material()
    value = {field: REF}
    item = _item(ref="fact:other", value=value)
    if field == "authority_type":
        item["source_bindings"][0][field] = REF
        bindings = tuple(
            ResolvedSourceBinding.model_validate(binding) for binding in item["source_bindings"]
        )
        item["source_hash"] = source_bindings_hash(bindings)
    assert (
        resolve_cited_pinned_material(
            context=_context(item, name="relevant_facts"), manifest=manifest, ref=REF
        )
        is None
    )


def test_absent_legacy_marker_and_frozen_request_are_preserved():
    manifest, draft = _legacy_material()
    assert "pinned_source_materials_version" not in manifest.model_dump(mode="json")
    item = {"item_ref": "candidate:unverified", "value": {"text": "I mentioned " + REF}}
    context = _context(item, name="active_memory_candidates")
    legacy = resolve_cited_pinned_material(context=context, manifest=manifest, ref=REF, version="1")
    assert legacy is not None
    assert legacy["materials"] == [{"slice": "active_memory_candidates", "item": item}]
    assert resolve_cited_pinned_material(context=context, manifest=manifest, ref=REF) is None
    with pytest.raises(ValueError, match="unknown pinned source"):
        resolve_cited_pinned_material(context=context, manifest=manifest, ref=REF, version="3")
    with pytest.raises(ValueError, match="pinned_source_materials_version"):
        type(manifest).model_validate_json(json.dumps(
            manifest.model_dump(mode="json", exclude_computed_fields=True)
            | {"pinned_source_materials_version": "3"}
        ))


def test_production_capability_offers_only_resolvable_context_sources(tmp_path):
    from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
    from companion_daemon.world_v2.life_development_capability import (
        ProjectionLifeCapabilityManifestCompiler,
    )
    from companion_daemon.world_v2.local_chronology import LocalChronology
    from test_life_development_production import _open_life_seed, _wake

    wake = _wake()
    item = _item()
    injected = "biography:candidate-injection"
    context = _context(item)
    context["slices"]["active_memory_candidates"] = {
        "availability": "available",
        "items": [
            _item(
                ref=injected,
                value={
                    "reviewed_timeline_ref": injected,
                    "text": injected,
                },
            )
        ],
    }
    projection = SimpleNamespace(
        world_revision=7,
        deliberation_revision=3,
        ledger_sequence=11,
        committed_world_event_refs=(
            SimpleNamespace(event_id=wake.event_id, event_type=wake.event_type),
        ),
        npcs=(
            SimpleNamespace(
                npc_id="friend",
                status="active",
                privacy_class="personal",
                registration_event_ref="event:npc:registered",
            ),
        ),
    )
    capsule = SimpleNamespace(
        model_content_json=json.dumps(context),
        world_life=SimpleNamespace(
            availability="available", source_refs=("event:timeline-configured",)
        ),
        active_memory_candidates=SimpleNamespace(availability="available", source_refs=(injected,)),
    )
    catalog = ReviewedLifeSeedCatalog.from_yaml(
        path=_open_life_seed(tmp_path / "seed.yaml"),
        chronology=LocalChronology("Asia/Shanghai"),
    )
    manifest = ProjectionLifeCapabilityManifestCompiler(
        owner_actor_ref="actor:companion", catalog=catalog
    ).compile(
        projection=projection,
        wake=wake,
        capsule=capsule,
    )
    assert manifest.pinned_source_materials_version == "2"
    assert injected not in manifest.grounding_refs
    assert "event:npc:registered" in manifest.grounding_refs
    assert "npc:friend" in manifest.entity_refs
    for ref in (REF, "reviewed-biography:timeline"):
        assert ref in manifest.grounding_refs
        assert (
            resolve_cited_pinned_material(context=context, manifest=manifest, ref=ref) is not None
        )


def test_activity_material_keeps_its_intention_and_unfinished_scope():
    manifest, _ = _legacy_material()
    ref = "event:activity:planned"
    value = {
        "context_kind": "planned_activity",
        "activity_event_ref": ref,
        "plan_id": "plan:self-directed",
        "owner_actor_ref": "actor:companion",
        "activity_kind": "self_directed",
        "privacy_class": "personal",
        "scheduled_window": {
            "opens_at": "2026-07-29T11:00:00Z",
            "closes_at": "2026-07-29T12:00:00Z",
        },
        "accepted_intention": {
            "content_ref": "content:plan",
            "content_payload_hash": "c" * 64,
            "text": "I plan to sort my notes.",
            "truncated": False,
            "epistemic_scope": "accepted_intention_only_not_embedded_history_or_outcome",
        },
        "planning_scope": "accepted_plan_not_started_or_completed",
        "proposal_source": {
            "authority_event_ref": "event:character:proposal",
            "authority_ledger_sequence": 1,
            "authority_payload_hash": "b" * 64,
        },
        "source_bindings": [
            {
                "authority_event_ref": ref,
                "authority_world_revision": 1,
                "authority_payload_hash": "a" * 64,
            }
        ],
    }
    item = _item(ref=ref, value=value)
    material = resolve_cited_pinned_material(context=_context(item), manifest=manifest, ref=ref)
    assert material is not None
    assert material["materials"][0]["item"]["value"] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", [None, "2"])
async def test_public_runtime_rejects_candidate_material_even_with_historical_manifest(reader):
    from companion_daemon.world_v2.ledger import WorldLedger
    from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore
    from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
    from test_life_development_runtime import (
        WORLD_ID,
        OWNER,
        _PinnedCapsuleCompiler,
        _SequenceModel,
        _StaticManifestCompiler,
        _novel_book_exchange_draft,
        _seed_clock,
    )

    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    value = _novel_book_exchange_draft(wake=wake)
    value["claim_declarations"][0].update(scope="existing_world", source_refs=[REF])
    item = _item(ref="candidate:unverified", value={"text": "I mentioned " + REF})

    class ManifestCompiler(_StaticManifestCompiler):
        def compile(self, **kwargs):
            manifest = super().compile(**kwargs)
            return manifest.model_copy(
                update={
                    "grounding_refs": tuple(sorted({*manifest.grounding_refs, REF})),
                    "pinned_source_materials_version": reader,
                }
            )

    author = _SequenceModel(model="world-author", outputs=(json.dumps(value),))
    character = _SequenceModel(
        model="character", outputs=(AssertionError("must not reach character"),)
    )
    runtime = LifeDevelopmentRuntime(
        ledger=ledger,
        content_store=InMemoryImmutableLifeContentStore(),
        world_author=author,
        character_interior=character,
        capsule_compiler=_PinnedCapsuleCompiler(
            ledger=ledger, context=_context(item, name="active_memory_candidates")
        ),
        capability_manifest_compiler=ManifestCompiler(wake=wake),
        owner_actor_ref=OWNER,
    )
    result = await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:source-authority",
        correlation_id="correlation:source-authority",
    )
    assert result.status == "technical_failure"
    assert result.reason_code == "life_development.source_closure_evidence_unavailable"
    assert author.calls == 1
    assert character.calls == 0
    assert character.consider_calls == 0
