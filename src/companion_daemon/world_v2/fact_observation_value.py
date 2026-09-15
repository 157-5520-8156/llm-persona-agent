"""Exact value selection for the installed observation-substring Fact producer.

This verifies bytes chosen by a model, without searching for a value, judging a
predicate or treating the enclosing Observation as an accepted Fact value.
"""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from .schema_core import FrozenModel


class FactObservationValueBinding(FrozenModel):
    contract: Literal['fact-observation-value-binding.1'] = 'fact-observation-value-binding.1'
    value_ref: str
    value_hash: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def reference_matches_hash(self):
        if self.value_ref != 'value:observation:' + self.value_hash:
            raise ValueError('Fact value binding is not the installed observation value reference')
        return self

    @classmethod
    def from_fact_values(cls, values):
        if (values.assertion_binding.source_kind != 'observed_message'
            or values.value_ref != 'value:observation:' + values.value_hash):
            return None
        return cls(value_ref=values.value_ref, value_hash=values.value_hash)

    def select(self, *, source_excerpt: str, quoted_value: str) -> str:
        checked = type(self).model_validate_json(self.model_dump_json())
        if (not isinstance(quoted_value, str) or not 1 <= len(quoted_value) <= 256
            or quoted_value not in source_excerpt
            or hashlib.sha256(quoted_value.encode('utf-8')).hexdigest() != checked.value_hash):
            raise ValueError('selected quote is not the exact accepted Fact value')
        return quoted_value
