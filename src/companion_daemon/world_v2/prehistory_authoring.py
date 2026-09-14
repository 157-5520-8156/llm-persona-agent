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


def _bounded_packet(value: dict) -> dict:
    size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if size > MAX_AUTHORING_PACKET_BYTES:
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
    "检查是否夹带当前角色内心/用户关系/未来行为脚本、真实用户未确认的共同往事，"
    "或把启动后事件伪装成历史。新增片段应有普通生活的多样性和联系。"
    "结构校验通过不代表语义通过；不能只凭格式与哈希批准。"
    "只评审给定内容，不替作者润色或补充事实；有问题则退回修改后重新审核。"
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


class PrehistoryCreationReview(FrozenModel):
    contract: Literal["prehistory-creation-review.1"] = "prehistory-creation-review.1"
    brief_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_ref: str = Field(min_length=1)
    reviewed_at: datetime
    decision: Literal["approved", "rejected"]
    records: tuple[PrehistoryRecordVerdict, ...] = Field(min_length=1, max_length=256)
    cross_record_findings: tuple[str, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def verdict_is_consistent(self):
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("review time must be timezone aware")
        if len({item.record_id for item in self.records}) != len(self.records):
            raise ValueError("review duplicates a record")
        all_approved = not self.cross_record_findings and all(item.verdict == "approve" for item in self.records)
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
