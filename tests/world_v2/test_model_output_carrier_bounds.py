"""Completed inbound evidence has its own bound; raw proposals keep theirs."""
import pytest

from companion_daemon.world_v2.character_interior.inbound_output_record import _recorded_output
from companion_daemon.world_v2.deliberation import (
    MAX_MODEL_OUTPUT_BYTES, MAX_MODEL_OUTPUT_NODES, MAX_VISIBLE_SOURCE_REVIEW_BYTES,
    ModelOutput, _checked_output,
)


@pytest.mark.parametrize("as_dict", [False, True])
def test_completed_review_carrier_does_not_consume_raw_output_allowance(as_dict):
    # This boundary transports opaque evidence; full receipt authority is checked
    # separately. Its preflight must honor the declared independent carrier size.
    review = "界" * (MAX_MODEL_OUTPUT_BYTES // 3 + 1)
    output = ModelOutput(model_id="fixture", model_version="fixture.1", raw_proposal={}, visible_source_review_json=review)
    value = output.model_dump(mode="python") if as_dict else output
    assert _checked_output(value).visible_source_review_json == review
    assert _recorded_output(output).visible_source_review_json == review


@pytest.mark.parametrize("kind", ["raw", "nodes", "review_bytes", "review_type"])
def test_separate_carrier_never_widens_raw_or_hostile_adapter_bounds(kind):
    value = dict(model_id="fixture", model_version="fixture.1", raw_proposal={}, visible_source_review_json="review")
    if kind == "raw":
        value['raw_proposal'] = {'text': 'x' * MAX_MODEL_OUTPUT_BYTES}
    elif kind == "nodes":
        value['raw_proposal'] = {'items': [None] * MAX_MODEL_OUTPUT_NODES}
    elif kind == "review_bytes":
        value['visible_source_review_json'] = '界' * (MAX_VISIBLE_SOURCE_REVIEW_BYTES // 3 + 1)
    else:
        value['visible_source_review_json'] = {'text': 'x' * MAX_MODEL_OUTPUT_BYTES}
    with pytest.raises(ValueError):
        _checked_output(ModelOutput.model_construct(**value))
