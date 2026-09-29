"""Source-supported links between actor-scoped prehistory recollections.

Links help ordinary Recall reconnect scenes. They do not assert causation,
grant a memory candidate, or expose another actor's private reading.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .prehistory_life_materials import LifeMaterialsDraft
from .schema_core import FrozenModel


StoryRelationKind = Literal[
    "shared_scene",
    "episode_followup",
    "later_disclosure",
    "explicit_callback",
]
MAX_STORY_LINK_RELATIONS = 24


class StoryLinkEvidence(FrozenModel):
    block_id: str = Field(min_length=1, max_length=256)
    source_quote: str = Field(min_length=1, max_length=1_200)


class PrehistoryStoryRelation(FrozenModel):
    left_record_id: str = Field(min_length=1, max_length=256,
        description="First scene in the narrative when order is known; otherwise an endpoint of an undirected association.")
    right_record_id: str = Field(min_length=1, max_length=256,
        description="Later scene when order is known; otherwise the other endpoint of an undirected association.")
    relation_kind: StoryRelationKind
    evidence: tuple[StoryLinkEvidence, ...] = Field(min_length=2, max_length=6)
    bridge_source_quote: str = Field(min_length=1, max_length=1_200,
        description="Exact original-narrative text that explicitly connects the two scenes; chronology alone is insufficient.")
    rationale: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def identities_are_distinct(self):
        if self.left_record_id == self.right_record_id:
            raise ValueError("story-link record endpoints must be distinct")
        if len({item.block_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("story-link evidence block IDs must be unique")
        return self


class PrehistoryStoryLinkDraft(FrozenModel):
    contract: Literal["prehistory-story-links.1"] = "prehistory-story-links.1"
    actor_ref: str = Field(min_length=1, max_length=256)
    source_materials_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    relations: tuple[PrehistoryStoryRelation, ...] = Field(max_length=MAX_STORY_LINK_RELATIONS)

    @model_validator(mode="after")
    def relation_pairs_are_unique(self):
        pairs = tuple(frozenset((item.left_record_id, item.right_record_id)) for item in self.relations)
        if len(pairs) != len(set(pairs)):
            raise ValueError("story-link record pairs must be unique, even when proposed relation kinds differ")
        return self


class PrehistoryStoryLinkResponse(FrozenModel):
    """Model-authored associations; the host adds actor and material identity."""

    contract: Literal["prehistory-story-link-response.1"] = "prehistory-story-link-response.1"
    relations: tuple[PrehistoryStoryRelation, ...] = Field(max_length=MAX_STORY_LINK_RELATIONS)


class PrehistoryStoryLinkArtifact(FrozenModel):
    """Immutable request/graph/output binding produced by the story-link runner."""

    contract: Literal["prehistory-story-link-artifact.1"] = "prehistory-story-link-artifact.1"
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_artifact_ref: str = Field(min_length=1, max_length=512)
    source_materials_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    linked_materials_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft: PrehistoryStoryLinkDraft
    dropped_duplicate_relation_count: int = Field(default=0, ge=0)
    origin: Literal["model_generated", "approved_review_carry_forward"] = "model_generated"
    prior_story_link_artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prior_review_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    carried_relation_indexes: tuple[int, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def origin_has_matching_provenance(self):
        if len(set(self.carried_relation_indexes)) != len(self.carried_relation_indexes):
            raise ValueError("carried relation indexes must be unique")
        if self.origin == "model_generated":
            if self.prior_story_link_artifact_hash is not None or self.prior_review_hash is not None or self.carried_relation_indexes:
                raise ValueError("model-generated graph cannot claim carry-forward provenance")
        elif self.prior_story_link_artifact_hash is None or self.prior_review_hash is None:
            raise ValueError("approved carry-forward graph must bind its prior artifact and review")
        elif len(self.carried_relation_indexes) != len(self.draft.relations):
            raise ValueError("every carried relation must have a source review index")
        return self


STORY_LINK_INSTRUCTION = (
    "为同一actor已经抽取的prehistory recollections生成有限的叙事关联，输出严格JSON。"
    "原文是完整操作材料；候选记录仅代表指定actor确实有权知道的内容。"
    "每个关系必须有原文中的明确跨事件连接语句，并把其中逐字连续的原文写入bridge_source_quote。"
    "时间先后、共同人物、相似主题、常见人生阶段、推测的因果或‘后来’单词本身都不足以连接；"
    "episode_followup必须有原文明确说明后一段是同一件具体事情的后续阶段；日期、共同人物、关系延续或一般主题都不算。"
    "不要创建横跨数年、以友情或职业为主题的宽泛continuing arc；离散生活事件可以保持互不连接。"
    "later_disclosure要求原文明示后来把早先某件事告诉该actor；单纯晚些时候谈到相关话题不算。"
    "同一无序记录对最多输出一个关系类型，选择证据最明确、最具体的一类；不确定就省略。"
    f"整篇只输出最多{MAX_STORY_LINK_RELATIONS}条关系，优先选择最能恢复核心故事线的明确回指、后续告知和同一场景；"
    "不要穷举普通时间顺序，孤立的日常片段可以保持孤立。"
    "每个关系还必须引用两条记录各自至少一个block_id，并逐字摘录这两个block的source_quote作为evidence。"
    "left/right在明确的前后关系中按叙事顺序排列；对无序关联也须只输出一次。"
    "不能添加新人物、事件、行动、日期、因果、内心或获知事实，也不能从别的actor记录取证。"
    "不确定时不建立关系。actor_ref、source_materials_hash由宿主绑定，请勿转写。"
    "只输出给定output_schema规定的JSON对象。"
)


def normalize_story_link_response(
    response: PrehistoryStoryLinkResponse,
) -> tuple[PrehistoryStoryLinkResponse, int]:
    """Keep at most one, most-specific model-proposed relation per record pair."""
    kept: dict[frozenset[str], PrehistoryStoryRelation] = {}
    duplicates = 0
    for relation in response.relations:
        key = frozenset((relation.left_record_id, relation.right_record_id))
        if key in kept:
            duplicates += 1
            continue
        kept[key] = relation
    return response.model_copy(update={"relations": tuple(kept.values())}), duplicates


def story_link_request(
    materials: LifeMaterialsDraft,
    actor_ref: str,
    *,
    narrative_text: str,
    reviewed_link_findings: tuple[str, ...] = (),
) -> dict[str, object]:
    materials = LifeMaterialsDraft.model_validate_json(materials.model_dump_json())
    records = tuple(item for item in materials.recollections if item.actor_ref == actor_ref)
    if not records:
        raise ValueError("story-link actor has no recollections")
    source_hash = hashlib.sha256(narrative_text.encode("utf-8")).hexdigest()
    if materials.source_artifact_ref != f"narrative#sha256={source_hash}":
        raise ValueError("story-link narrative text differs from the material's source artifact")
    blocks = {item.block_id: item for item in materials.blocks}
    cards = []
    for record in records:
        cards.append({
            "record_id": record.record_id,
            "actor_ref": record.actor_ref,
            "known_from": record.known_from.isoformat(),
            "known_until": record.known_until.isoformat(),
            "time_precision": record.time_precision,
            "participant_refs": record.participant_refs,
            "block_evidence": tuple({
                "block_id": block.block_id,
                "kind": block.kind,
                "subject_actor_ref": block.subject_actor_ref,
                "source_locator": block.source_locator,
                "source_quote": block.source_quote,
                "actor_view_text": block.text,
            } for block in (blocks[item] for item in record.block_ids)),
        })
    instruction = STORY_LINK_INSTRUCTION
    if reviewed_link_findings:
        instruction += (
            "此前独立审核已经拒绝过下列关系结论；这些是待避免的错误示例，不是事实，也不是要求生成替代关系："
            + json.dumps(reviewed_link_findings, ensure_ascii=False)
            + "。修订时不要重建同一对无依据链接；仍须只根据当前原文和当前记录建立关系。"
        )
    packet = {
        "contract": "prehistory-story-link-request.1",
        "instruction": instruction,
        "actor_ref": actor_ref,
        "source_artifact_ref": materials.source_artifact_ref,
        "source_narrative": narrative_text,
        "prior_independent_review_findings": reviewed_link_findings,
        "source_materials_hash": _materials_hash(materials),
        "records": tuple(cards),
        "output_schema": PrehistoryStoryLinkResponse.model_json_schema(),
    }
    serialized = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(serialized) > 120_000:
        raise ValueError("actor-scoped story-link packet exceeds its byte budget")
    return packet


def _materials_hash(materials: LifeMaterialsDraft) -> str:
    from .character_prehistory import digest

    return digest(materials)


def validate_story_links(
    materials: LifeMaterialsDraft,
    *,
    actor_ref: str,
    draft: PrehistoryStoryLinkDraft,
    narrative_text: str,
) -> PrehistoryStoryLinkDraft:
    materials = LifeMaterialsDraft.model_validate_json(materials.model_dump_json())
    draft = PrehistoryStoryLinkDraft.model_validate_json(draft.model_dump_json())
    if draft.actor_ref != actor_ref or draft.source_materials_hash != _materials_hash(materials):
        raise ValueError("story-link response does not bind the requested actor and materials")
    source_hash = hashlib.sha256(narrative_text.encode("utf-8")).hexdigest()
    if materials.source_artifact_ref != f"narrative#sha256={source_hash}":
        raise ValueError("story-link validation narrative differs from its source artifact")
    records = {item.record_id: item for item in materials.recollections}
    blocks = {item.block_id: item for item in materials.blocks}
    for relation in draft.relations:
        left, right = records.get(relation.left_record_id), records.get(relation.right_record_id)
        if left is None or right is None or left.actor_ref != actor_ref or right.actor_ref != actor_ref:
            raise ValueError("story-link relation crosses actor or record scope")
        left_blocks, right_blocks = set(left.block_ids), set(right.block_ids)
        evidence_left = evidence_right = False
        for evidence in relation.evidence:
            block = blocks.get(evidence.block_id)
            if block is None or actor_ref not in block.allowed_actor_refs:
                raise ValueError("story-link evidence block is unavailable to the requested actor")
            if evidence.source_quote not in block.source_quote:
                raise ValueError("story-link evidence is not an exact substring of its actor-view source quote")
            evidence_left |= evidence.block_id in left_blocks
            evidence_right |= evidence.block_id in right_blocks
        if not evidence_left or not evidence_right:
            missing = []
            if not evidence_left:
                missing.append(relation.left_record_id)
            if not evidence_right:
                missing.append(relation.right_record_id)
            raise ValueError(
                f"story-link {relation.left_record_id} -> {relation.right_record_id} lacks evidence from record(s): "
                + ", ".join(missing)
            )
        if relation.bridge_source_quote not in narrative_text:
            raise ValueError("story-link bridge quote is not an exact substring of the bound original narrative")
    return draft


def apply_story_links(
    materials: LifeMaterialsDraft,
    *,
    actor_ref: str,
    draft: PrehistoryStoryLinkDraft,
    narrative_text: str,
) -> LifeMaterialsDraft:
    materials = LifeMaterialsDraft.model_validate_json(materials.model_dump_json())
    draft = validate_story_links(materials, actor_ref=actor_ref, draft=draft, narrative_text=narrative_text)
    additions: dict[str, set[str]] = {}
    for relation in draft.relations:
        additions.setdefault(relation.left_record_id, set()).add(relation.right_record_id)
        additions.setdefault(relation.right_record_id, set()).add(relation.left_record_id)
    recollections = tuple(
        record.model_copy(update={
            "related_record_refs": tuple(sorted(set(record.related_record_refs) | additions.get(record.record_id, set())))
        })
        for record in materials.recollections
    )
    return LifeMaterialsDraft.model_validate({
        **materials.model_dump(),
        "recollections": recollections,
    })


def carry_forward_approved_story_links(
    *,
    previous_source_materials: LifeMaterialsDraft,
    previous_linked_materials: LifeMaterialsDraft,
    previous_archive,
    previous_artifact: PrehistoryStoryLinkArtifact,
    previous_review,
    current_materials: LifeMaterialsDraft,
    current_archive,
    actor_ref: str,
    narrative_text: str,
) -> tuple[LifeMaterialsDraft, PrehistoryStoryLinkArtifact, dict[str, object]]:
    """Carry only independently approved links whose endpoints and evidence are unchanged.

    This is a source-safe recovery path for a failed new link-generation call.
    It never creates a relation; it reuses a prior independently approved one
    only when current record content and all cited blocks are byte-equivalent.
    """
    from .character_prehistory import PrehistoryArchiveDocument, digest
    from .prehistory_authoring import PrehistoryCreationReview

    previous_source_materials = LifeMaterialsDraft.model_validate_json(previous_source_materials.model_dump_json())
    previous_linked_materials = LifeMaterialsDraft.model_validate_json(previous_linked_materials.model_dump_json())
    current_materials = LifeMaterialsDraft.model_validate_json(current_materials.model_dump_json())
    previous_archive = PrehistoryArchiveDocument.model_validate_json(previous_archive.model_dump_json())
    current_archive = PrehistoryArchiveDocument.model_validate_json(current_archive.model_dump_json())
    previous_artifact = PrehistoryStoryLinkArtifact.model_validate_json(previous_artifact.model_dump_json())
    previous_review = PrehistoryCreationReview.model_validate_json(previous_review.model_dump_json())

    if previous_artifact.origin != "model_generated":
        raise ValueError("carry-forward source must be an original model-generated link artifact")
    if previous_review.decision == "approved":
        # Fully approved graphs can be copied only through the same exact-content
        # checks below; decision alone never authorizes a modified endpoint.
        pass
    if (previous_artifact.draft.actor_ref, previous_review.document_hash) != (
        actor_ref, digest(previous_archive),
    ):
        raise ValueError("prior story-link artifact and review differ from the selected actor/archive")
    if previous_source_materials.source_artifact_ref != current_materials.source_artifact_ref:
        raise ValueError("carry-forward cannot cross original narrative sources")
    if previous_artifact.source_artifact_ref != previous_source_materials.source_artifact_ref:
        raise ValueError("prior story-link artifact has a different narrative source")
    if previous_artifact.source_materials_hash != digest(previous_source_materials):
        raise ValueError("prior story-link artifact does not bind the supplied source materials")
    reconstructed_previous = apply_story_links(
        previous_source_materials, actor_ref=actor_ref, draft=previous_artifact.draft,
        narrative_text=narrative_text,
    )
    if digest(reconstructed_previous) != digest(previous_linked_materials):
        raise ValueError("prior linked materials differ from their captured story-link artifact")
    if previous_artifact.linked_materials_hash != digest(previous_linked_materials):
        raise ValueError("prior story-link artifact does not bind its linked materials")
    if previous_archive.source_artifact_ref != f"life-materials:sha256:{digest(previous_linked_materials)}":
        raise ValueError("prior archive does not bind the approved linked materials")
    if current_archive.source_artifact_ref != f"life-materials:sha256:{digest(current_materials)}":
        raise ValueError("current archive does not bind the revised source materials")
    if (previous_archive.world_id, previous_archive.actor_ref) != (current_archive.world_id, current_archive.actor_ref):
        raise ValueError("carry-forward cannot cross archive owner or World")
    previous_records = {record.record_id: record for record in previous_archive.records}
    current_records = {record.record_id: record for record in current_archive.records}
    if {item.record_id: item.record_hash for item in previous_review.records} != {
        record.record_id: digest(record) for record in previous_archive.records
    }:
        raise ValueError("prior independent review does not cover the prior archive exactly")
    if len(previous_review.story_links) != len(previous_artifact.draft.relations):
        raise ValueError("prior independent review does not cover each story relation")

    review_rows = {item.relation_index: item for item in previous_review.story_links}
    if set(review_rows) != set(range(len(previous_artifact.draft.relations))):
        raise ValueError("prior link review indexes do not cover the captured relation graph")
    prior_blocks = {block.block_id: block for block in previous_linked_materials.blocks}
    current_blocks = {block.block_id: block for block in current_materials.blocks}
    retained: list[tuple[int, PrehistoryStoryRelation]] = []
    dropped: list[dict[str, object]] = []
    for index, relation in enumerate(previous_artifact.draft.relations):
        verdict = review_rows[index]
        if (verdict.left_record_id, verdict.right_record_id, verdict.relation_kind) != (
            relation.left_record_id, relation.right_record_id, relation.relation_kind,
        ):
            raise ValueError("prior link review index no longer matches its exact relation")
        if verdict.verdict != "approve":
            dropped.append({"relation_index": index, "reason": "previous_independent_review_rejected"})
            continue
        endpoints = (relation.left_record_id, relation.right_record_id)
        if any(record_id not in previous_records or record_id not in current_records for record_id in endpoints):
            dropped.append({"relation_index": index, "reason": "endpoint_record_removed"})
            continue
        if any(
            previous_records[record_id].model_copy(update={"related_record_refs": ()})
            != current_records[record_id].model_copy(update={"related_record_refs": ()})
            for record_id in endpoints
        ):
            dropped.append({"relation_index": index, "reason": "endpoint_record_changed"})
            continue
        if any(
            evidence.block_id not in prior_blocks or evidence.block_id not in current_blocks
            or prior_blocks[evidence.block_id] != current_blocks[evidence.block_id]
            for evidence in relation.evidence
        ):
            dropped.append({"relation_index": index, "reason": "cited_source_block_changed"})
            continue
        retained.append((index, relation))

    draft = PrehistoryStoryLinkDraft(
        actor_ref=actor_ref, source_materials_hash=digest(current_materials),
        relations=tuple(relation for _, relation in retained),
    )
    draft = validate_story_links(current_materials, actor_ref=actor_ref, draft=draft,
                                 narrative_text=narrative_text)
    linked_materials = apply_story_links(current_materials, actor_ref=actor_ref, draft=draft,
                                         narrative_text=narrative_text)
    carry_request = {
        "contract": "prehistory-story-link-carry-forward-request.1",
        "actor_ref": actor_ref,
        "source_artifact_ref": current_materials.source_artifact_ref,
        "prior_story_link_artifact_hash": digest(previous_artifact),
        "prior_independent_review_hash": digest(previous_review),
        "prior_archive_hash": digest(previous_archive),
        "current_archive_hash": digest(current_archive),
        "current_materials_hash": digest(current_materials),
        "carried_relation_indexes": tuple(index for index, _ in retained),
        "dropped_relations": tuple(dropped),
    }
    request_hash = hashlib.sha256(json.dumps(
        carry_request, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    artifact = PrehistoryStoryLinkArtifact(
        request_hash=request_hash,
        source_artifact_ref=current_materials.source_artifact_ref,
        source_materials_hash=digest(current_materials),
        linked_materials_hash=digest(linked_materials),
        draft=draft,
        origin="approved_review_carry_forward",
        prior_story_link_artifact_hash=digest(previous_artifact),
        prior_review_hash=digest(previous_review),
        carried_relation_indexes=tuple(index for index, _ in retained),
    )
    manifest = {
        "contract": "prehistory-story-link-carry-forward-run.1",
        "status": "completed",
        "provider_calls": 0,
        "origin": artifact.origin,
        "request_hash": request_hash,
        "prior_story_link_artifact_hash": digest(previous_artifact),
        "prior_review_hash": digest(previous_review),
        "source_materials_hash": digest(current_materials),
        "linked_materials_hash": digest(linked_materials),
        "carried_relation_indexes": tuple(index for index, _ in retained),
        "dropped_relations": tuple(dropped),
        "links": len(draft.relations),
        "request": carry_request,
    }
    return linked_materials, artifact, manifest


__all__ = [
    "PrehistoryStoryLinkArtifact",
    "PrehistoryStoryLinkDraft",
    "PrehistoryStoryLinkResponse",
    "PrehistoryStoryRelation",
    "StoryLinkEvidence",
    "apply_story_links",
    "normalize_story_link_response",
    "story_link_request",
    "validate_story_links",
]
