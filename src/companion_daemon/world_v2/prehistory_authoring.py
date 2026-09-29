"""Offline creation/review packages, separate from live character memory.

Operator-provided review provenance is trusted input, not proof that a model or
person really reviewed a document. Import still enforces the original World.
No provider, ledger write, retention choice or runtime behavior lives here.
"""
from __future__ import annotations

from datetime import datetime
import json
from typing import Literal

from pydantic import Field, model_validator

from .character_prehistory import (
    PrehistoryArchiveDocument, PrehistoryReview, ReviewedPrehistoryArchive, digest,
)
from .schema_core import FrozenModel

MAX_AUTHORING_PACKET_BYTES = 96_000
MAX_SEMANTIC_REVIEW_PACKET_BYTES = 256_000


def _bounded_packet(value: dict, *, max_bytes: int = MAX_AUTHORING_PACKET_BYTES) -> dict:
    size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if size > max_bytes:
        raise ValueError("authoring/review packet exceeds its byte budget; split work without dropping evidence")
    return value


AUTHOR_INSTRUCTION = (
    "创作一个虚构角色在原始World启动前的人生档案，返回指定schema的JSON。"
    "profile是已配置设定，accepted_archives是已经成立的历史；不得改写或冲突。"
    "创建普通片段、反复经历和少量转折，给人物地点稳定身份并关联事件；"
    "不是逐日模拟，也不要求每件事戏剧化。新增细节是本次待审核创作，不是旧聊天的证据。"
    "不要根据未交付、被拒绝的草稿补造对应经历。"
    "记录发生了什么；不要预写角色现在的感受、动机、对用户的态度或未来行为。"
    "主观解释留给角色；档案不决定她记住、遗忘或讲述什么。"
    "不得创作与真实用户的共同历史，不得把历史人物变成当前NPC或在场者。"
    "时间严格早于world_started_at；不确定就用区间精度，不伪造具体日期。"
)
REVIEW_INSTRUCTION = (
    "独立审核待创作档案，逐条给出approve或reject及理由，并列出跨记录问题。"
    "核对profile与accepted_archives的一致性、年龄/学年/地点/人物身份、时间先后、"
    "关联记录、隐私、重复或互相矛盾的事件。所有新记录都要覆盖。"
    "必须逐项核对每个句子对所属actor是否可知：亲历、亲眼所见，或原文明示对方曾告诉该actor才可记录。"
    "第三人称叙述者讲出一个人物的工作、日常、私下想法或合作冲突，并不等于主角知道；"
    "‘她有些事会告诉朋友’不能证明任何特定细节已经告诉朋友。仅共享参与者、认识同一个人或同场出现也不是知识来源。"
    "一条记录混有可知和不可知的细节时，拒绝该条，要求按actor视角拆分/删除，而不是用相邻对话推断知情。"
    "检查是否夹带当前角色内心/用户关系/未来行为脚本、真实用户未确认的共同往事，"
    "或把启动后事件伪装成历史。新增片段应有普通生活的多样性和联系。"
    "结构校验通过不代表语义通过；不能只凭格式与哈希批准。"
    "只评审给定内容，不替作者润色或补充事实；有问题则退回修改后重新审核。"
    "cross_record_findings只记录跨记录的事实冲突，不放单条记录或关系边的判断；单条关系边逐项写入story_links。"
    "没有跨记录事实冲突时cross_record_findings必须为[]。"
    "只有每条记录、每条提交的关系边均approve且cross_record_findings为空，decision才为approved；"
    "其他情况必须为rejected。逐条审核依据写入对应rationale，不将推断说成已有设定。"
)


class PrehistoryAuthoringBrief(FrozenModel):
    contract: Literal["prehistory-authoring-brief.1"] = "prehistory-authoring-brief.1"
    world_id: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    world_started_at: datetime
    world_start_event_ref: str = Field(min_length=1)
    world_start_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    born_at: datetime
    created_at: datetime
    author_ref: str = Field(min_length=1)
    draft_source_ref: str = Field(min_length=1)
    profile_source_ref: str = Field(min_length=1)
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile: dict[str, object]
    accepted_archives: tuple[PrehistoryArchiveDocument, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def target_is_bounded(self):
        for instant in (self.born_at, self.world_started_at, self.created_at):
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError("authoring dates must be timezone aware")
        if not self.born_at < self.world_started_at:
            raise ValueError("birth must precede original World start")
        for archive in self.accepted_archives:
            if archive.world_id != self.world_id or archive.actor_ref != self.actor_ref:
                raise ValueError("accepted archive has a different owner or World")
            if any(record.occurred_until >= self.world_started_at for record in archive.records):
                raise ValueError("accepted archive exceeds original World start")
        return self


def validate_draft(brief: PrehistoryAuthoringBrief, document: PrehistoryArchiveDocument) -> None:
    """Structural coverage only. No inference about narrative consistency."""
    brief = PrehistoryAuthoringBrief.model_validate_json(brief.model_dump_json())
    document = PrehistoryArchiveDocument.model_validate_json(document.model_dump_json())
    if (document.world_id, document.actor_ref, document.source_artifact_ref) != (
        brief.world_id, brief.actor_ref, brief.draft_source_ref,
    ):
        raise ValueError("draft differs from bound World, actor or creation source")
    existing_records = {r.record_id for archive in brief.accepted_archives for r in archive.records}
    existing_entities = {e.entity_ref: e for archive in brief.accepted_archives for e in archive.entities}
    if document.archive_id in {archive.archive_id for archive in brief.accepted_archives}:
        raise ValueError("draft cannot replace an accepted archive")
    proposed = {record.record_id for record in document.records}
    if proposed & existing_records:
        raise ValueError("draft cannot rewrite an accepted record")
    for entity in document.entities:
        if entity.entity_ref in existing_entities and entity != existing_entities[entity.entity_ref]:
            raise ValueError("draft cannot rename an accepted historical identity")
    for record in document.records:
        if not brief.born_at <= record.occurred_from <= record.occurred_until < brief.world_started_at:
            raise ValueError("draft interval lies outside the character pre-start lifetime")
        if set(record.related_record_refs) - proposed - existing_records:
            raise ValueError("draft relates to an unknown historical record")


def author_request(brief: PrehistoryAuthoringBrief) -> dict:
    return _bounded_packet({"contract": "prehistory-author-request.1", "instruction": AUTHOR_INSTRUCTION,
            "brief": brief.model_dump(mode="json"), "brief_hash": digest(brief),
            "output_schema": PrehistoryArchiveDocument.model_json_schema()})


class PrehistoryRecordVerdict(FrozenModel):
    record_id: str
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: Literal["approve", "reject"]
    rationale: str = Field(min_length=1, max_length=4096)


class PrehistorySemanticLinkVerdict(FrozenModel):
    relation_index: int = Field(ge=0, le=127, strict=True,
        description="Zero-based position in the submitted story_link_artifact.draft.relations array.")
    verdict: Literal["approve", "reject"]
    rationale: str = Field(min_length=1, max_length=4096)


class PrehistoryLinkVerdict(FrozenModel):
    relation_index: int = Field(ge=0, le=127, strict=True)
    left_record_id: str = Field(min_length=1, max_length=256)
    right_record_id: str = Field(min_length=1, max_length=256)
    relation_kind: str = Field(min_length=1, max_length=64)
    verdict: Literal["approve", "reject"]
    rationale: str = Field(min_length=1, max_length=4096)


class PrehistoryCreationReview(FrozenModel):
    contract: Literal["prehistory-creation-review.1"] = "prehistory-creation-review.1"
    brief_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_ref: str = Field(min_length=1)
    reviewed_at: datetime
    decision: Literal["approved", "rejected"] = Field(
        description="approved iff every record is approve and cross_record_findings is empty; otherwise rejected",
    )
    records: tuple[PrehistoryRecordVerdict, ...] = Field(min_length=1, max_length=256)
    story_links: tuple[PrehistoryLinkVerdict, ...] = Field(default=(), max_length=128)
    cross_record_findings: tuple[str, ...] = Field(
        default=(), max_length=256,
        description="Blocking cross-record problems only. Empty [] when none; never approval explanations, positive summaries or nonblocking suggestions.",
    )

    @model_validator(mode="after")
    def verdict_is_consistent(self):
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("review time must be timezone aware")
        if len({item.record_id for item in self.records}) != len(self.records):
            raise ValueError("review duplicates a record")
        if len({item.relation_index for item in self.story_links}) != len(self.story_links):
            raise ValueError("review duplicates a story-link index")
        all_approved = (
            not self.cross_record_findings
            and all(item.verdict == "approve" for item in self.records)
            and all(item.verdict == "approve" for item in self.story_links)
        )
        if (self.decision == "approved") != all_approved:
            raise ValueError("review decision disagrees with record or cross-record findings")
        return self


def review_request(brief: PrehistoryAuthoringBrief, document: PrehistoryArchiveDocument) -> dict:
    validate_draft(brief, document)
    return _bounded_packet({"contract": "prehistory-review-request.1", "instruction": REVIEW_INSTRUCTION,
            "brief": brief.model_dump(mode="json"), "brief_hash": digest(brief),
            "document": document.model_dump(mode="json"), "document_hash": digest(document),
            "record_hashes": {record.record_id: digest(record) for record in document.records},
            "output_schema": PrehistoryCreationReview.model_json_schema()})


class PrehistorySemanticRecordVerdict(FrozenModel):
    record_index: int = Field(ge=0, le=255, strict=True,
        description="Zero-based position in the submitted document.records array.")
    verdict: Literal["approve", "reject"]
    rationale: str = Field(min_length=1, max_length=4096)


class PrehistorySemanticReview(FrozenModel):
    """Model judgment only; the caller owns request identity and provenance."""

    contract: Literal["prehistory-semantic-review.1"] = "prehistory-semantic-review.1"
    decision: Literal["approved", "rejected"] = Field(
        description="approved iff every record is approve and cross_record_findings is empty; otherwise rejected",
    )
    records: tuple[PrehistorySemanticRecordVerdict, ...] = Field(min_length=1, max_length=256)
    story_links: tuple[PrehistorySemanticLinkVerdict, ...] = Field(default=(), max_length=128,
        description="If a story-link artifact is supplied, judge every relation by its zero-based array index exactly once.")
    cross_record_findings: tuple[str, ...] = Field(default=(), max_length=256,
        description="Blocking cross-record problems only; [] when none. No positive summaries.")

    @model_validator(mode="after")
    def verdict_is_consistent(self):
        if len({item.record_index for item in self.records}) != len(self.records):
            raise ValueError("review duplicates a record index")
        if len({item.relation_index for item in self.story_links}) != len(self.story_links):
            raise ValueError("review duplicates a story-link index")
        all_approved = (
            not self.cross_record_findings
            and all(item.verdict == "approve" for item in self.records)
            and all(item.verdict == "approve" for item in self.story_links)
        )
        if (self.decision == "approved") != all_approved:
            raise ValueError("review decision disagrees with record or cross-record findings")
        return self


def semantic_review_request(
    brief: PrehistoryAuthoringBrief,
    document: PrehistoryArchiveDocument,
    *,
    story_links=None,
    narrative_text: str | None = None,
    source_materials=None,
) -> dict:
    """Bind the independent reviewer to the exact source, actor view, archive and links."""
    packet = review_request(brief, document)
    instruction = REVIEW_INSTRUCTION
    narrative_source_ref = None
    if narrative_text is not None:
        import hashlib

        narrative_source_ref = f"narrative#sha256={hashlib.sha256(narrative_text.encode('utf-8')).hexdigest()}"
        packet["narrative_source_artifact_ref"] = narrative_source_ref
        packet["narrative_text"] = narrative_text
    if source_materials is not None:
        from .prehistory_life_materials import LifeMaterialsDraft, export_actor_archive

        source_materials = LifeMaterialsDraft.model_validate_json(source_materials.model_dump_json())
        if narrative_source_ref is None or source_materials.source_artifact_ref != narrative_source_ref:
            raise ValueError("semantic review materials do not bind the supplied original narrative")
        materials_hash = digest(source_materials)
        expected_archive_ref = f"life-materials:sha256:{materials_hash}"
        if document.source_artifact_ref != expected_archive_ref:
            raise ValueError("reviewed archive does not bind the supplied source materials")
        rebuilt = export_actor_archive(source_materials, document.actor_ref, document.archive_id)
        if digest(rebuilt) != digest(document):
            raise ValueError("reviewed archive differs from the actor-scoped source materials")
        packet["source_materials"] = source_materials.model_dump(mode="json")
    if story_links is not None and (narrative_text is None or source_materials is None):
        raise ValueError("story-link review requires the bound original narrative and source materials")
    if story_links is not None:
        from .prehistory_story_links import PrehistoryStoryLinkArtifact

        story_links = PrehistoryStoryLinkArtifact.model_validate_json(story_links.model_dump_json())
        source_hash = document.source_artifact_ref.removeprefix("life-materials:sha256:")
        record_ids = {record.record_id for record in document.records}
        archive_pairs = {
            frozenset((record.record_id, related))
            for record in document.records for related in record.related_record_refs
        }
        link_pairs = {
            frozenset((relation.left_record_id, relation.right_record_id))
            for relation in story_links.draft.relations
        }
        if (
            story_links.draft.actor_ref != document.actor_ref
            or story_links.source_artifact_ref != narrative_source_ref
            or story_links.linked_materials_hash != source_hash
            or story_links.draft.source_materials_hash != story_links.source_materials_hash
            or any(not pair <= record_ids for pair in link_pairs)
            or archive_pairs != link_pairs
        ):
            raise ValueError("story-link artifact does not match the reviewed actor and archive links")
        packet["story_link_artifact"] = story_links.model_dump(mode="json")
        packet["story_link_artifact_hash"] = digest(story_links)
        instruction += (
            "request包含原始叙事全文、source_materials、主角可读视角及待审归档。逐句核对归档文本与原文和对应source_quote；"
            "字面引用存在不等于主角确实知道该事实。检查allowed_actor_refs是否有原文明确支持，区分亲历、亲眼所见、明确告知、主角本人的解释与全知叙述者的私有信息。"
            "模糊地说‘有些事告诉过她’不证明任何特定细节曾告知；除非文中有明确传达，不得把他人的工作安排、私下合作/争执、内心或个人财务当成主角记忆。"
            "混合可知和不可知内容的记录应拒绝。"
            "同时逐条审核story_link_artifact：bridge_source_quote必须确实连接两段，且关系类型与原文一致；仅共同人物、相似主题、时间先后或一般友情延续不能证明一条故事关系。"
            "episode_followup只允许同一件具体事情明确进入下一阶段；不能用宽泛的友情、职业阶段或人生年代关联离散事件。"
            "story_links必须按story_link_artifact.draft.relations数组的0-based下标逐条返回relation_index，每条恰好一次；"
            "不成立的边标reject并在对应rationale说明，不得用cross_record_findings代替逐边审核。"
            "只有所有记录与story_links都approve且不存在跨记录事实冲突时，decision才是approved。"
            "不能因为一条记录的主体事实正确就放过夹带细节。"
        )
        if story_links.origin == "approved_review_carry_forward":
            instruction += (
                "该artifact标明origin=approved_review_carry_forward：这些边来自先前独立批准、且端点与出处未变化的关系。"
                "这只是来源说明，不替代本次审核；仍需逐条按当前原文、当前记录和当前source_quote独立approve或reject。"
            )
    instruction += (
        "按document.records的从0开始的数组下标逐条返回record_index；"
        "每个下标必须出现一次。不输出哈希、记录ID、审核者或时间，这些由调用方绑定实际输入。"
    )
    packet.update(contract="prehistory-semantic-review-request.1",
        instruction=instruction, output_schema=PrehistorySemanticReview.model_json_schema())
    return _bounded_packet(packet, max_bytes=MAX_SEMANTIC_REVIEW_PACKET_BYTES)


def bind_semantic_review(
    submitted_request: dict, response: PrehistorySemanticReview, *,
    reviewer_ref: str, reviewed_at: datetime,
) -> PrehistoryCreationReview:
    """Bind to the submitted packet, never a fresh read of the mutable draft.

    This offline boundary trusts caller-provided request/response pairing and
    reviewer metadata, just like operator-supplied reviews. A provider runner must
    independently capture and verify that pairing before claiming real review.
    It cannot rehabilitate a failed hash-bearing legacy review.
    """
    _bounded_packet(submitted_request, max_bytes=MAX_SEMANTIC_REVIEW_PACKET_BYTES)
    brief = PrehistoryAuthoringBrief.model_validate_json(json.dumps(submitted_request["brief"]))
    document = PrehistoryArchiveDocument.model_validate_json(json.dumps(submitted_request["document"]))
    story_links = None
    if "story_link_artifact" in submitted_request:
        from .prehistory_story_links import PrehistoryStoryLinkArtifact

        story_links = PrehistoryStoryLinkArtifact.model_validate_json(
            json.dumps(submitted_request["story_link_artifact"], ensure_ascii=False),
        )
    narrative_text = submitted_request.get("narrative_text")
    source_materials = None
    if "source_materials" in submitted_request:
        from .prehistory_life_materials import LifeMaterialsDraft

        source_materials = LifeMaterialsDraft.model_validate_json(
            json.dumps(submitted_request["source_materials"], ensure_ascii=False),
        )
    if submitted_request != semantic_review_request(
        brief, document, story_links=story_links, narrative_text=narrative_text,
        source_materials=source_materials,
    ):
        raise ValueError("submitted semantic review packet differs from its bound content or contract")
    response = PrehistorySemanticReview.model_validate_json(response.model_dump_json())
    by_index = {item.record_index: item for item in response.records}
    if set(by_index) != set(range(len(document.records))):
        raise ValueError("semantic review must cover every submitted record index exactly once")
    expected_link_count = len(story_links.draft.relations) if story_links is not None else 0
    by_link_index = {item.relation_index: item for item in response.story_links}
    if set(by_link_index) != set(range(expected_link_count)):
        raise ValueError("semantic review must cover every submitted story-link index exactly once")
    if reviewer_ref == brief.author_ref:
        raise ValueError("author cannot supply the separate reviewer identity")
    if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None or reviewed_at < brief.created_at:
        raise ValueError("review time must be aware and cannot predate the authoring brief")
    return PrehistoryCreationReview(
        brief_hash=digest(brief), document_hash=digest(document),
        reviewer_ref=reviewer_ref, reviewed_at=reviewed_at, decision=response.decision,
        cross_record_findings=response.cross_record_findings,
        records=tuple(PrehistoryRecordVerdict(
            record_id=record.record_id, record_hash=digest(record),
            verdict=by_index[index].verdict, rationale=by_index[index].rationale,
        ) for index, record in enumerate(document.records)),
        story_links=tuple(PrehistoryLinkVerdict(
            relation_index=index, left_record_id=relation.left_record_id,
            right_record_id=relation.right_record_id, relation_kind=relation.relation_kind,
            verdict=by_link_index[index].verdict, rationale=by_link_index[index].rationale,
        ) for index, relation in enumerate(story_links.draft.relations if story_links is not None else ())),
    )


def package_reviewed(
    brief: PrehistoryAuthoringBrief, document: PrehistoryArchiveDocument,
    review: PrehistoryCreationReview, *, review_artifact_ref: str,
) -> ReviewedPrehistoryArchive:
    """Bind operator-supplied review to exact content; never import or retain."""
    validate_draft(brief, document)
    review = PrehistoryCreationReview.model_validate_json(review.model_dump_json())
    if (review.brief_hash, review.document_hash) != (digest(brief), digest(document)):
        raise ValueError("review does not bind the current brief and document")
    if review.reviewer_ref == brief.author_ref:
        raise ValueError("author cannot supply the separate reviewer identity")
    if review.reviewed_at < brief.created_at:
        raise ValueError("review predates the authoring brief")
    if {item.record_id: item.record_hash for item in review.records} != {
        record.record_id: digest(record) for record in document.records
    }:
        raise ValueError("review must cover every exact record once")
    if review.decision != "approved":
        raise ValueError("rejected draft cannot become an import package")
    return ReviewedPrehistoryArchive(document=document, review=PrehistoryReview(
        manifest_hash=digest(document.manifest()), reviewer_ref=review.reviewer_ref,
        review_artifact_ref=review_artifact_ref, review_artifact_hash=digest(review),
        reviewed_at=review.reviewed_at, decision="approved",
    ))
