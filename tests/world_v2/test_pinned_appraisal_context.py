"""Native private history survives working-context selection without drift."""
from copy import deepcopy
from datetime import datetime
import json

import pytest

from companion_daemon.world_v2.context_capsule import ContextCapsule
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger_context_resolver import ContextRelevanceScope, context_capsule_compiler_from_ledger
from companion_daemon.world_v2.living_state_inventory import install_living_state_context
from companion_daemon.world_v2.pinned_appraisal_context import compile_pinned_appraisals
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from test_affect_acceptance_runtime import _runtime
from test_visible_source_composer import _request


def compiled():
    runtime, _ = _runtime()
    ledger = runtime.ledger
    projection = ledger.project()
    query = query_from_projection(projection, actor_ref="actor:companion", trigger_ref="test:history")
    # This working-context scope omits the interpreted user. The private owner
    # still possesses the accepted reading about that user.
    scope = ContextRelevanceScope(actor_ref="actor:companion")
    old = context_capsule_compiler_from_ledger(ledger=ledger, relevance_scope=scope).compile(query)
    compiler = context_capsule_compiler_from_ledger(
        ledger=ledger, relevance_scope=scope, retain_pinned_appraisals=True,
    )
    return ledger, projection, query, old, compiler.compile(query)


def test_native_inventory_survives_omitted_working_lane_and_is_shared_by_readers():
    _, projection, _, old, capsule = compiled()
    assert old.pinned_appraisals is None
    assert old.model_content_json == capsule.model_content_json
    assert not capsule.appraisals.items
    inventory = capsule.pinned_appraisals
    assert len(inventory.records) == 1
    context = install_living_state_context(
        json.loads(capsule.model_content_json), projection, pinned_appraisals=inventory,
    )
    authored = context["slices"]["living_appraisals"]["items"]
    table = compile_visible_source_table(request=_request(capsule), capsule=capsule, include_subjective_history=True)
    reviewed = {r["review_material"]["item"]["item_ref"]: r["review_material"]["item"]["value"]
                for r in table.source_references() if r["review_material"].get("lane") == "appraisals"}
    assert set(reviewed) == {a["source_ref"] for a in authored}
    for item in authored:
        value = reviewed[item["source_ref"]]
        assert value["hypotheses"] == item["value"]["hypotheses"]
        assert datetime.fromisoformat(value["accepted_at"]) == datetime.fromisoformat(item["value"]["accepted_at"])
    assert any(r.get("subjective_history_support") for r in table.source_references())
    assert ContextCapsule.model_validate_json(capsule.model_dump_json()) == capsule


@pytest.mark.parametrize("fault", ["body", "actor", "cursor", "event", "snapshot"])
def test_inventory_is_part_of_the_complete_compiler_pin(fault):
    _, _, _, _, capsule = compiled()
    raw = capsule.model_dump(mode="json")
    inventory = raw["pinned_appraisals"]
    if fault == "body":
        inventory["records"][0]["value"]["hypotheses"][0]["meaning"] = "different private interpretation"
    elif fault == "actor":
        inventory["owner_actor_ref"] = "other:companion"
    elif fault == "cursor":
        inventory["cursor"]["ledger_sequence"] += 1
    elif fault == "event":
        inventory["records"][0]["source"]["payload_hash"] = "f" * 64
    else:
        inventory["snapshot_hash"] = "f" * 64
    with pytest.raises(ValueError):
        ContextCapsule.model_validate_json(json.dumps(raw))


def test_native_value_mismatch_is_not_promoted_from_projection():
    ledger, projection, query, _, _ = compiled()
    changed = deepcopy(projection.appraisals[0])
    changed = changed.model_copy(update={"confidence_bp": 1})
    projection = projection.model_copy(update={"appraisals": (changed,)})
    with pytest.raises(ValueError, match="accepted native value"):
        compile_pinned_appraisals(projection=projection, query=query, ledger=ledger)
