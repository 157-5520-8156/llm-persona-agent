from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

AuditClassification = Literal["mechanical", "semi_automatic", "human_only"]
AuditState = Literal["occurred", "opportunity_no_occurrence", "no_opportunity"]


@dataclass(frozen=True)
class BehaviorInstance:
    behavior_id: str
    seq: int | None
    quote: str
    context: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BehaviorVerdict:
    behavior_id: str
    title: str
    classification: AuditClassification
    state: AuditState
    criteria: dict[str, Any]
    opportunity_count: int
    occurred_count: int
    instances: tuple[BehaviorInstance, ...] = ()
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["instances"] = [item.as_dict() for item in self.instances]
        return payload


@dataclass(frozen=True)
class BehaviorAuditResult:
    world_id: str
    database: str
    max_seq: int
    seq_window: tuple[int, int]
    verdicts: tuple[BehaviorVerdict, ...]
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "world_id": self.world_id,
            "database": self.database,
            "max_seq": self.max_seq,
            "seq_window": list(self.seq_window),
            "generated_at": self.generated_at,
            "verdicts": [item.as_dict() for item in self.verdicts],
        }
