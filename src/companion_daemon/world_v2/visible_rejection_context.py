"""Exact rejected expression, carried as correction data, never world evidence."""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from .schema_core import FrozenModel


class RejectedVisibleBeat(FrozenModel):
    beat_index: int = Field(ge=0, le=15)
    text: str = Field(min_length=1, max_length=4096)


class RejectedVisibleExpression(FrozenModel):
    contract: Literal["rejected-visible-expression.1"] = "rejected-visible-expression.1"
    authority: Literal["rejected_candidate_not_world_evidence"] = "rejected_candidate_not_world_evidence"
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_table_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    beats: tuple[RejectedVisibleBeat, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def complete_order(self):
        if [b.beat_index for b in self.beats] != list(range(len(self.beats))):
            raise ValueError("rejected expression must retain every beat in original order")
        return self


def rejected_expression_from_review(prepared) -> RejectedVisibleExpression:
    """Only the verified review preparation supplies the original candidate."""
    pin = prepared.as_dict()
    return RejectedVisibleExpression(
        candidate_sha256=hashlib.sha256(pin["candidate_json"].encode()).hexdigest(),
        source_table_sha256=hashlib.sha256(pin["source_table_json"].encode()).hexdigest(),
        beats=tuple(RejectedVisibleBeat(beat_index=b["beat_index"], text=b["text"])
                    for b in pin["beat_mapping"]),
    )
