import json
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.life_development_draft import LifeDevelopmentPossibilityDraft
from companion_daemon.world_v2.world_consequence_author_tool import world_consequence_author_tool_contract, _wire_identity
from companion_daemon.world_v2.world_consequence_contract import WorldConsequenceAuthority, WorldConsequenceAuthorCursor
from companion_daemon.world_v2.world_consequence_prompt import validate_world_consequence_offered_bindings
from test_life_development_runtime import OWNER, WORLD_ID, _seed_clock
from test_world_consequence_producer import _draft


def candidate():
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    value = _draft(wake)
    claim = "local:claim:attempt-result"
    value["claim_declarations"].append({"claim_id": claim, "summary": "The authorized attempt has a candidate objective result.",
        "scope": "novel_world_generation", "subject_scope": "authorized_attempt_result", "source_refs": []})
    binding = {"source_kind": "activity_execution", "source_event_type": "ActivityStarted", "actor_ref": OWNER,
        "source_event_ref": "event:bounded-start", "source_world_revision": 1, "source_payload_hash": "a" * 64,
        "privacy_class": "shareable", "plan_id": "plan:bounded", "activity_id": "activity:bounded", "plan_entity_revision": 2}
    for outcome in value["outcomes"]:
        outcome["claim_refs"] = sorted([*outcome["claim_refs"], claim])
        outcome["world_consequence"]["authorized_attempt_result"] = {"text": "原定尝试到达了入口，但没有完成浏览。", "execution_binding": binding}
    return value, binding


def test_generated_result_requires_an_exact_offered_binding_without_prior_success_proof():
    value, binding = candidate()
    draft = LifeDevelopmentPossibilityDraft.model_validate_json(json.dumps(value))
    authority = WorldConsequenceAuthority.model_validate_json(json.dumps({"world_id": WORLD_ID, "actor_ref": OWNER,
        "evaluated_cursor": WorldConsequenceAuthorCursor(world_revision=1, deliberation_revision=0, ledger_sequence=1).model_dump(),
        "execution_bindings": [binding]}))
    messages = [{"role": "user", "content": json.dumps({"execution_authority": authority.model_dump(mode="json")})}]
    validate_world_consequence_offered_bindings(draft=draft, messages=messages)
    messages[0]["content"] = json.dumps({"execution_authority": authority.model_copy(update={"execution_bindings": ()}).model_dump(mode="json")})
    with pytest.raises(ValueError, match="exact execution binding"):
        validate_world_consequence_offered_bindings(draft=draft, messages=messages)


@pytest.mark.parametrize("fault", ["future_plan", "premise", "missing_binding", "prior_fact"])
def test_attempt_declaration_cannot_grant_generic_character_history_or_new_choices(fault):
    value, _ = candidate()
    if fault == "future_plan":
        value["causal_authority"] = "character_choice"
    elif fault == "premise":
        value["premise_claim_refs"].append("local:claim:attempt-result")
        value["premise_claim_refs"].sort()
    elif fault == "missing_binding":
        value["outcomes"][0]["world_consequence"].pop("authorized_attempt_result")
    else:
        value["claim_declarations"][-1].update(scope="existing_world", source_refs=["event:any"])
    with pytest.raises(ValueError):
        LifeDevelopmentPossibilityDraft.model_validate_json(json.dumps(value))


def test_v4_wire_remains_frozen_and_v5_alone_offers_bounded_result_subject():
    provider = SimpleNamespace(supports_required_tool_choice=True, supports_strict_tool_choice=True)
    legacy = world_consequence_author_tool_contract(provider=provider, contract_id="world-consequence-author-tool.4")
    current = world_consequence_author_tool_contract(provider=provider)
    assert _wire_identity(legacy)["tool_contract_sha256"] == "b6e7580a428a28276f76b0307123066770c8928de7c8f0fc40a0f00717623741"
    assert '"authorized_attempt_result"' in json.dumps(current)
    assert _wire_identity(current)["contract"] == "world-consequence-author-tool.5"
