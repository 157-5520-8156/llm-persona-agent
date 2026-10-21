from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.life_development_output_schema import life_possibility_output_schema
from companion_daemon.world_v2.world_consequence_contract import (
    WorldConsequenceAuthority, WorldConsequenceAuthorCursor,
)
from companion_daemon.world_v2.world_consequence_prompt import compile_world_consequence_messages


def _inputs():
    authority = WorldConsequenceAuthority(
        world_id="world:prompt", actor_ref="actor:companion",
        evaluated_cursor=WorldConsequenceAuthorCursor(
            world_revision=1, deliberation_revision=0, ledger_sequence=1,
        ),
    )
    context = {
        "capability_manifest": {
            "owner_actor_ref": authority.actor_ref,
            "pinned_cursor": authority.evaluated_cursor.model_dump(mode="json"),
            "outcome_contract": "world-consequence.2",
        },
        "cross_field_authority": {"outcome_text": {"what_she_did_in_her_world": True}},
        "disturbance_consequence_usage_specimen": {"outcome_text": "old action example"},
        "pinned_world_context": {"original_source": "The hail damaged a tree."},
    }
    return authority, context


@pytest.mark.parametrize("wrapped", [False, True])
def test_missing_nested_fields_do_not_hide_wrong_claim_namespace(wrapped):
    from datetime import UTC, datetime
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentCapabilityManifest, LifeDevelopmentDraftError
    from companion_daemon.world_v2.life_development_runtime import _world_author_repair_coordinates
    from companion_daemon.world_v2.schemas import ProjectionCursor

    draft = {"claim_declarations": [{"claim_id": "local:claim:scene"}],
             "premise_claim_refs": ["event:source"],
             "outcomes": [{"claim_refs": ["local:claim:scene"],
                           "visual_evidence": {"objects": [{"description": "object"}]}}]}
    raw = json.dumps({"replacement": draft} if wrapped else draft)
    error = LifeDevelopmentDraftError("invalid_shape", "nested object lacks local_ref",
        violations=({"path": "outcomes.0.visual_evidence.objects.0.local_ref",
                     "type": "missing", "message": "Field required"},))
    manifest = LifeDevelopmentCapabilityManifest(version="life-development-capability.test",
        owner_actor_ref="actor:test", pinned_cursor=ProjectionCursor(
            world_revision=1, deliberation_revision=0, ledger_sequence=1),
        outcome_contract="world-consequence.2", max_future_days=30, max_window_minutes=720)
    coordinates = _world_author_repair_coordinates(raw=raw, error=error, manifest=manifest,
        hard_boundary_contract={}, logical_time=datetime(2026, 10, 19, tzinfo=UTC))
    closure = next(c for c in coordinates if c["rule"] == "claim_declaration_exact_closure")
    assert closure["undeclared_claim_refs"] == ["event:source"]
    assert closure["declared_claim_ids"] == ["local:claim:scene"]
    assert json.loads(raw) == ({"replacement": draft} if wrapped else draft)


def test_current_author_wire_explains_separate_reference_namespaces():
    authority, context = _inputs()
    messages = compile_world_consequence_messages(user_context=context, authority=authority, execution_materials=())
    assert "claim_declarations[*].claim_id" in messages[0]["content"]
    assert "claim_declarations[*].source_refs" in messages[0]["content"]


def test_disturbance_prompt_preserves_every_supported_durable_effect_lane():
    authority, context = _inputs()
    context["occasion_mode"] = "disturbance"
    guidance = compile_world_consequence_messages(user_context=context, authority=authority, execution_materials=())[0]["content"]
    assert "provisional NPC/place" in guidance
    assert "at least one outcome needs dynamic_life_direction" not in guidance


def test_historical_output_schema_keeps_the_exact_pre_protocol_fingerprint():
    # Independently recovered from 4fed62c8, including original key insertion
    # order and required-text semantics. New replay never upgrades this wire.
    schema = life_possibility_output_schema()
    digest = hashlib.sha256(json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    assert digest == "04ed2aa92f4fbd96d66fbcc65b116ad5c427b673abc8945035efc553cace5720"


def test_current_prompt_requires_new_consequences_and_removes_old_action_permissions():
    authority, context = _inputs()
    before = deepcopy(context)
    messages = compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=(),
    )
    assert context == before
    user = json.loads(messages[1]["content"])
    assert user["execution_authority"] == authority.model_dump(mode="json")
    assert user["execution_materials"] == []
    assert user["pinned_world_context"] == context["pinned_world_context"]
    assert "disturbance_consequence_usage_specimen" not in user
    assert "outcome_text" not in user["cross_field_authority"]
    assert "what_she_did_in_her_world" not in messages[0]["content"] + messages[1]["content"]
    outcome = user["output_contract"]["propose"]["$defs"]["LifeDevelopmentOutcomeDraft"]
    assert "text" not in outcome["properties"]
    assert "world_consequence" in outcome["required"]
    assert outcome["properties"]["world_consequence"] == {"$ref": "#/$defs/WorldConsequenceV2"}


def test_cached_prefix_preserves_all_values_and_the_system_level_of_request_guidance():
    authority, context = _inputs()
    old = compile_world_consequence_messages(user_context=context, authority=authority, execution_materials=())
    new = compile_world_consequence_messages(user_context=context, authority=authority, execution_materials=(), cache_stable_prefix=True)
    assert [m["role"] for m in new] == ["system", "user", "system"]
    assert json.loads(old[1]["content"]) == json.loads(new[1]["content"])
    assert new[2]["content"] in old[0]["content"]
    assert "capability_manifest.anchor_refs is empty" in new[2]["content"]
    assert "following system message" in new[0]["content"]
    assert len(old) == 2


def test_new_application_uses_cache_layout_with_equal_world_material_and_longer_prefix():
    from os.path import commonprefix
    from inspect import signature
    from companion_daemon.world_v2.production_turn_application import WorldV2TurnApplicationConfig
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    config = WorldV2TurnApplicationConfig(world_id='world:prompt',
        companion_actor_ref='actor:companion', reply_target='user:test', action_pump_owner='pump:test')
    assert config.world_author_cache_stable_prefix
    assert signature(build_qq_c2c_host).parameters['world_author_cache_stable_prefix'].default is True
    old, new = [], []
    for revision in (1, 2):
        authority, context = _inputs()
        authority = authority.model_copy(update={'evaluated_cursor':
            authority.evaluated_cursor.model_copy(update={'world_revision':revision, 'ledger_sequence':revision})})
        context['capability_manifest']['pinned_cursor'] = authority.evaluated_cursor.model_dump(mode='json')
        before = deepcopy(context)
        plain = compile_world_consequence_messages(user_context=context, authority=authority, execution_materials=())
        cached = compile_world_consequence_messages(user_context=context, authority=authority, execution_materials=(),
            cache_stable_prefix=config.world_author_cache_stable_prefix)
        assert context == before
        assert json.loads(plain[1]['content']) == json.loads(cached[1]['content'])
        assert cached[2]['role'] == 'system' and cached[2]['content'] in plain[0]['content']
        old.append(json.dumps(plain, ensure_ascii=False))
        new.append(json.dumps(cached, ensure_ascii=False))
    assert len(commonprefix(new)) > len(commonprefix(old))


@pytest.mark.parametrize("wrong", ["owner", "cursor", "legacy"])
def test_current_prompt_cannot_relabel_another_or_historical_manifest(wrong):
    authority, context = _inputs()
    manifest = context["capability_manifest"]
    if wrong == "owner":
        manifest["owner_actor_ref"] = "actor:other"
    elif wrong == "cursor":
        manifest["pinned_cursor"]["ledger_sequence"] += 1
    else:
        manifest.pop("outcome_contract")
    with pytest.raises(ValueError, match="exact execution material"):
        compile_world_consequence_messages(
            user_context=context, authority=authority, execution_materials=(),
        )

def test_current_prompt_uses_one_offered_reference_list_and_teaches_windows():
    authority, context = _inputs()
    context["capability_manifest"].update(
        {
            "anchor_refs": ["event:anchor:offered"],
            "max_window_minutes": 720,
        }
    )
    context["occasion_mode"] = "disturbance"

    messages = compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=(),
    )
    system = messages[0]["content"]

    packet = json.loads(messages[1]['content'])
    assert packet['capability_manifest']['anchor_refs'] == ['event:anchor:offered']
    assert 'event:anchor:offered' not in system
    assert 'event:anchor:1' not in system
    assert 'location:reviewed-place' not in system
    assert "capability_manifest.max_window_minutes" in system
    assert "location_capability_coordinates" in system
    assert 'dynamic_life_direction' in system


def test_existing_pressure_does_not_turn_an_ordinary_opening_into_a_disturbance():
    authority, context = _inputs()
    context.update(occasion_mode="ordinary", pressure_surfaces=[{"summary": "ongoing repairs"}])
    messages = compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=(),
    )
    assert "This is a disturbance occasion" not in messages[0]["content"]
    assert json.loads(messages[1]["content"])["pressure_surfaces"] == context["pressure_surfaces"]
    context["occasion_mode"] = "disturbance"
    assert "This is a disturbance occasion" in compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=(),
    )[0]["content"]


def test_independent_scene_view_excludes_chat_agenda_without_changing_original_history():
    from companion_daemon.world_v2.world_environment_attention import independent_scene_context
    context = {"slices": {"current_situation": {"plans": ["personal task"]},
                          "relevant_facts": {"private_user_report": "test"},
                          "world_life": {"items": [{"source_ref": "original-world", "value": "unchanged"}]}}}
    before = deepcopy(context)
    visible = independent_scene_context(context)
    assert set(visible["slices"]) == {"world_life"}
    assert visible["slices"]["world_life"] == context["slices"]["world_life"]
    assert context == before


def test_completed_activity_prompt_stabilizes_only_the_contract_prefix():
    authority, context = _inputs()
    context["capability_manifest"]["completed_activity_consequence"] = {
        "contract": "completed-activity-consequence.1"
    }
    context.update(
        {
            "authored_subject": {"owner_actor_ref": authority.actor_ref},
            "claim_classification_contract": {"contract": "claims.1"},
            "world_author_wire": {"contract": "world-author.1"},
            "recent_life_texture": {"evidence": ["a completed day"]},
        }
    )

    messages = compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=(),
    )
    rendered = json.loads(messages[1]["content"])

    assert list(rendered)[:4] == [
        "output_contract",
        "claim_classification_contract",
        "authored_subject",
        "world_author_wire",
    ]
    assert rendered["recent_life_texture"] == {"evidence": ["a completed day"]}
    assert rendered["pinned_world_context"] == context["pinned_world_context"]
    assert rendered["execution_authority"] == authority.model_dump(mode="json")
    assert rendered["execution_materials"] == []


def test_active_attempt_prompt_retains_its_original_dynamic_first_order():
    authority, context = _inputs()
    context["capability_manifest"]["active_attempt_consequence"] = {
        "contract": "active-attempt-consequence.1"
    }

    messages = compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=(),
    )

    assert list(json.loads(messages[1]["content"]))[0] == "capability_manifest"


def test_recent_history_index_keeps_outcomes_separate_from_identity_and_current_state():
    authority, context = _inputs()
    consequence = {
        "contract": "world-consequence.2",
        "environment": {"epistemic_scope": "settled_world_environment", "text": "公共设施开放时间变更。"},
        "authorized_attempt_result": {"text": "只整理完一半。", "execution_binding": {"source_event_ref": "event:started"}},
    }
    history = {"source_ref": "occurrence:one", "lane": "world_life", "value": {
        "settled_at": "2026-09-26T18:00:00Z", "privacy_class": "private", "location_ref": None,
        "content": {"content_ref": "content:one", "truncated": False, "world_consequence": consequence,
                    "descriptor_payload_hash": "a" * 64, "character_response": {"text": "她觉得有些麻烦", "epistemic_scope": "private_response"}},
    }}
    legacy = {"source_ref": "experience:legacy", "lane": "recent_experiences", "value": {"summary": "原始记录"}}
    context["recent_life_texture"] = {"contract": "recent-life-texture.1", "items": [
        {"source_ref": "biography:one", "lane": "world_life", "value": {"context_kind": "biographical_context", "age": 21}},
        history, legacy,
    ]}
    context["pinned_world_context"] = {"original_rows": deepcopy(context["recent_life_texture"]["items"])}
    before = deepcopy(context)
    rendered = json.loads(compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=(),
    )[1]["content"])
    assert context == before
    assert rendered["pinned_world_context"] == before["pinned_world_context"]
    texture = rendered["recent_life_texture"]
    assert texture["contract"] == "recent-life-texture.2"
    assert len(texture["items"]) == 2
    row = texture["items"][0]
    assert row["world_consequence"] == consequence
    assert row["settled_at"] == history["value"]["settled_at"]
    assert row["privacy_class"] == "private"
    assert row["location_ref"] is None
    assert row["truncated"] is False
    assert row["character_response"] == history["value"]["content"]["character_response"]
    assert texture["items"][1] == legacy


def test_bound_detail_prompt_cannot_offer_a_new_character_choice_or_independent_scene():
    from test_world_environment_attention import context as scene_context
    authority, context = scene_context()
    context['capability_manifest'].update(anchor_refs=['event:activated'], event_detail_capability={'source_kind':'world_occurrence'})
    messages = compile_world_consequence_messages(user_context=context, authority=authority, execution_materials=())
    packet = json.loads(messages[1]['content'])
    assert '"causal_authority": "character_choice"' not in messages[0]['content']
    assert 'environment_attention' not in packet
    assert 'independent ordinary scene opening' not in messages[0]['content']
    assert packet['capability_manifest'] == context['capability_manifest']
    assert packet['pinned_world_context'] == context['pinned_world_context']
    assert packet['output_contract']['propose']['properties']['causal_authority']['const'] == 'world_contingency'


def test_required_course_detail_prompt_has_exact_timing_without_no_op():
    authority, context = _inputs()
    context['capability_manifest']['event_detail_capability'] = {
        'source_kind': 'world_occurrence', 'required_program': True,
        'closing_offset_seconds': 5340,
    }
    messages = compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=())
    packet = json.loads(messages[1]['content'])
    assert 'no_op' not in packet['output_contract']
    assert 'no_op remains valid' not in messages[0]['content']
    assert 'after_seconds=0' in messages[0]['content']
    assert 'after_seconds=5340' in messages[0]['content']
    assert 'protagonist' in messages[0]['content']
    assert 'narrative:' in messages[0]['content']
    assert 'outcome.claim_refs' in messages[0]['content']


def test_ordinary_course_prompt_narrows_only_the_new_event_capability():
    authority, context = _inputs()
    context['capability_manifest']['event_detail_capability'] = {
        'contract': 'event-detail-capability.2', 'source_kind': 'world_occurrence',
        'required_program': True, 'closing_offset_seconds': 5940, 'privacy_class': 'private',
    }
    messages = compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=())
    packet = json.loads(messages[1]['content'])
    schema = packet['output_contract']['propose']
    assert packet['world_author_prompt_contract'] == 'world-consequence-prompt.4'
    assert schema['properties']['location_ref'] == {'type': 'null'}
    assert schema['properties']['privacy_class']['const'] == 'private'
    assert schema['$defs']['LifeDevelopmentOutcomeDraft']['properties']['visual_evidence'] == {'type': 'null'}
    assert 'no_op' not in packet['output_contract']
    assert 'Fate may schedule a separate encounter' in messages[0]['content']


def test_new_course_prompt_requires_lesson_progress_and_exact_now_timing():
    from jsonschema import Draft202012Validator

    authority, context = _inputs()
    context['capability_manifest'].update({
        'world_author_output_contract': '2',
        'event_detail_capability': {
            'contract': 'event-detail-capability.2', 'source_kind': 'world_occurrence',
            'required_program': True, 'closing_offset_seconds': 5940,
            'privacy_class': 'private',
        },
    })
    messages = compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=())
    packet = json.loads(messages[1]['content'])
    timing = packet['output_contract']['propose']['$defs']['LifeDevelopmentTimingDraft']
    assert packet['world_author_prompt_contract'] == 'world-consequence-prompt.9'
    assert timing['properties']['opens_at'] == timing['properties']['closes_at'] == {'type': 'null'}
    assert Draft202012Validator(timing).is_valid({
        'mode': 'now', 'duration_minutes': 100, 'opens_at': None, 'closes_at': None})
    assert not Draft202012Validator(timing).is_valid({
        'mode': 'now', 'duration_minutes': 100,
        'opens_at': '2026-10-21T08:15:00+08:00', 'closes_at': None})
    assert 'what is actually taught or done in this class' in messages[0]['content']
    assert 'companion attended' in messages[0]['content']


def test_current_prompt_deduplicates_identical_location_capabilities_only():
    authority, context = _inputs()
    capabilities = [{'location_ref':'room:one', 'capability_ref':'cap:one', 'privacy_class':'private'}]
    context['capability_manifest']['location_capabilities'] = capabilities
    context['cross_field_authority']['location_binding'] = {'available_capabilities':deepcopy(capabilities),'pairing':'both_or_neither'}
    before = deepcopy(context)
    packet = json.loads(compile_world_consequence_messages(user_context=context,authority=authority,execution_materials=())[1]['content'])
    assert context == before
    assert packet['capability_manifest']['location_capabilities'] == capabilities
    assert 'available_capabilities' not in packet['cross_field_authority']['location_binding']
    assert packet['cross_field_authority']['location_binding']['capabilities_source'] == 'capability_manifest.location_capabilities'
    context['cross_field_authority']['location_binding']['available_capabilities'][0]['privacy_class']='withhold'
    packet = json.loads(compile_world_consequence_messages(user_context=context,authority=authority,execution_materials=())[1]['content'])
    assert packet['cross_field_authority']['location_binding']['available_capabilities'][0]['privacy_class']=='withhold'


def test_detail_claim_correction_covers_the_same_fields_as_the_unchanged_validator():
    from types import SimpleNamespace
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentDraftError
    from companion_daemon.world_v2.life_development_runtime import _world_author_repair_coordinates
    error = LifeDevelopmentDraftError('invalid_shape','closure', violations=({'path':'<root>','message':'premise and outcomes must exactly close over claim declarations','type':'value_error'},))
    coordinates = _world_author_repair_coordinates(raw='{}',error=error,
        manifest=SimpleNamespace(event_detail_capability=object()),hard_boundary_contract={},logical_time=None)
    assert 'event_details.*.claim_refs' in coordinates[0]['field_paths']
    assert 'event_details[*].claim_refs' in coordinates[0]['required_relation']


def test_result_claim_permission_is_consistent_with_the_offered_attempt():
    from companion_daemon.world_v2.world_consequence_contract import ActivityExecutionBinding
    from companion_daemon.world_v2.world_consequence_execution_context import WorldConsequenceAuthorizedIntention, WorldConsequenceExecutionMaterial
    from companion_daemon.world_v2.life_development_runtime import _world_author_claim_classification_contract
    authority, context = _inputs()
    binding = ActivityExecutionBinding(actor_ref=authority.actor_ref,source_event_ref='start',source_world_revision=1,
        source_payload_hash='a'*64,privacy_class='private',source_event_type='ActivityStarted',plan_id='plan',activity_id='activity',plan_entity_revision=2)
    intention = WorldConsequenceAuthorizedIntention(text='整理书本',content_ref='text',content_payload_hash='b'*64,
        proposal_event_ref='proposal',proposal_payload_hash='c'*64,model_result_event_ref='model-event',
        model_result_payload_hash='d'*64,model_result_ref='model')
    material = WorldConsequenceExecutionMaterial(execution_binding=binding,status='available',authorized_intention=intention)
    authority = authority.model_copy(update={'execution_bindings':(binding,)})
    context['capability_manifest']['active_attempt_consequence'] = {'execution_binding':binding.model_dump(mode='json')}
    context['claim_classification_contract'] = _world_author_claim_classification_contract()
    context['cross_field_authority']['claim_declarations'] = {'novel_world_generation':{'allowed_subject_scopes':['provisional_entity','world_environment']}}
    packet = json.loads(compile_world_consequence_messages(user_context=context,authority=authority,execution_materials=(material,))[1]['content'])
    assert 'authorized_attempt_result' in packet['claim_classification_contract']['proposal_scoped_novel_world']['requirements']['subject_scope']
    assert 'authorized_attempt_result' in packet['cross_field_authority']['claim_declarations']['novel_world_generation']['allowed_subject_scopes']
    assert packet['execution_authority'] == authority.model_dump(mode='json')
    assert packet['execution_materials'] == [material.model_dump(mode='json')]
    consequence=packet['output_contract']['propose']['$defs']['WorldConsequenceV2']
    assert 'authorized_attempt_result' in consequence['required']
    assert consequence['properties']['authorized_attempt_result']=={'$ref':'#/$defs/AuthorizedAttemptResult'}
    timing=packet['output_contract']['propose']['$defs']['LifeDevelopmentTimingDraft']
    assert 'duration_minutes' in timing['required']
    assert timing['properties']['duration_minutes']['minimum']==5
    assert timing['properties']['opens_at']==timing['properties']['closes_at']=={'type':'null'}


def test_current_task_location_correction_cannot_change_to_later():
    from types import SimpleNamespace
    from test_life_development_runtime import _location_capability, NOW
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentDraftError
    from companion_daemon.world_v2.life_development_runtime import _world_author_repair_coordinates
    location = _location_capability()
    manifest = SimpleNamespace(location_capabilities=(location,),max_future_days=30,max_window_minutes=720,
        event_detail_capability=object(),active_attempt_consequence=None,completed_activity_consequence=None)
    failure=LifeDevelopmentDraftError('unsupported_location_window','test',failure_context={'selected_location_ref':location.location_ref})
    coordinates=_world_author_repair_coordinates(raw='{}',error=failure,manifest=manifest,hard_boundary_contract={},logical_time=NOW)
    assert coordinates[0]['selected_location_later_interval'] is None
    assert not any('later' in item for item in coordinates[0]['legal_repairs'])


def test_schema_compaction_preserves_domain_fields_and_validation_keywords():
    from companion_daemon.world_v2.world_consequence_prompt import _without_schema_annotations
    schema={'type':'object','title':'Display title','required':['title','default'],'additionalProperties':False,
            'properties':{'title':{'type':'string','minLength':1,'title':'Title','default':'sample'},
                          'default':{'type':'object','enum':[{'title':'literal','default':'literal'}]}},
            '$defs':{'text':{'type':'string','pattern':'^[ab]+$','title':'Annotation','maxLength':3}}}
    before=deepcopy(schema)
    compact=_without_schema_annotations(schema)
    assert schema==before
    assert compact['properties']['title']=={'type':'string','minLength':1}
    assert compact['properties']['default']['enum']==[{'title':'literal','default':'literal'}]
    assert compact['$defs']['text']=={'type':'string','pattern':'^[ab]+$','maxLength':3}
    assert compact['required']==schema['required'] and compact['additionalProperties'] is False


def test_unoffered_attempt_schema_is_null_only_without_removing_general_choice():
    from jsonschema import Draft202012Validator
    authority,context=_inputs()
    context['capability_manifest']['anchor_refs']=['event:offered']
    packet=json.loads(compile_world_consequence_messages(user_context=context,authority=authority,execution_materials=())[1]['content'])
    schema=packet['output_contract']['propose']
    Draft202012Validator.check_schema(schema)
    assert 'character_choice' in schema['properties']['causal_authority']['enum']
    assert schema['$defs']['WorldConsequenceV2']['properties']['authorized_attempt_result']=={'type':'null'}
    assert not {'AuthorizedAttemptResult','ActivityExecutionBinding','ReceiptExecutionBinding'} & schema['$defs'].keys()


def test_location_reselection_keeps_the_parsers_unlocated_option_and_visual_obligation():
    from companion_daemon.world_v2.ledger import WorldLedger
    from companion_daemon.world_v2.life_development_draft import (
        LifeDevelopmentDraftError, parse_world_author_draft,
    )
    from companion_daemon.world_v2.life_development_runtime import (
        _world_author_repair_coordinates, _world_author_reselection_instruction,
    )
    from test_life_development_runtime import (
        NOW, WORLD_ID, _manifest, _projection_cursor, _seed_clock,
    )
    from test_world_consequence_producer import _draft

    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger)).model_copy(
        update={'outcome_contract': 'world-consequence.2'}
    )
    assert manifest.location_capabilities
    value = _draft(wake)
    # Removing only the visual remains invalid for an ordinary located event.
    for outcome in value['outcomes']:
        outcome['visual_evidence'] = None
    with pytest.raises(LifeDevelopmentDraftError, match='must carry visual_evidence'):
        parse_world_author_draft(raw=json.dumps(value), manifest=manifest, logical_time=NOW)

    # A complete external alternative may genuinely have no location. Semantic
    # source review remains a separate gate; this control proves structural legality.
    value.update(location_ref=None, location_capability_ref=None, premise='本地交通票价公布调整。')
    value['claim_declarations'][0]['summary'] = '本地交通票价调整。'
    for outcome, text in zip(value['outcomes'], ('票价保持不变。', '票价上调一元。'), strict=True):
        outcome['world_consequence']['environment_text'] = text
    parsed = parse_world_author_draft(raw=json.dumps(value), manifest=manifest, logical_time=NOW)
    assert parsed.location_ref is None
    error = LifeDevelopmentDraftError('unsupported_location_window', 'closed')
    repair = _world_author_repair_coordinates(
        raw='{}', error=error, manifest=manifest, hard_boundary_contract={}, logical_time=NOW,
    )[0]
    assert 'complete_location_independent_replacement' in repair['legal_repairs']
    assert repair['illegal_repair'] == 'drop_location_fields_but_retain_unsupported_place_dependent_content'
    instruction = _world_author_reselection_instruction(failure_code=error.code)
    assert 'only when location_capabilities is empty' not in instruction
    assert 'place-dependent claims, prose and visuals' in instruction

    visual_error = LifeDevelopmentDraftError('invalid_shape', 'visual', violations=({
        'path': 'location_ref', 'message': 'outcome visual location must equal proposal',
        'type': 'value_error',
    },))
    visual_repair = _world_author_repair_coordinates(
        raw='{}', error=visual_error, manifest=manifest, hard_boundary_contract={}, logical_time=NOW,
    )[0]
    assert 'omit_visual_evidence' not in visual_repair['allowed_repairs']
    assert 'omit_visual_evidence_only_if_unlocated_or_withhold' in visual_repair['allowed_repairs']


def test_current_schema_exposes_canonical_coordinate_and_narrative_syntax():
    from jsonschema import Draft202012Validator
    authority, context = _inputs()
    packet = json.loads(compile_world_consequence_messages(user_context=context,
        authority=authority, execution_materials=())[1]["content"])
    assert packet["world_author_prompt_contract"] == "world-consequence-prompt.7"
    definitions = packet["output_contract"]["propose"]["$defs"]
    objective = definitions["ObjectiveBiographicalTransitionDraft"]["properties"]
    assert Draft202012Validator(objective["coordinate_ref"]).is_valid("biography:community.membership")
    assert not Draft202012Validator(objective["coordinate_ref"]).is_valid("biography.community.membership")
    for name in ("ObjectiveBiographicalTransitionDraft", "DynamicLifeDirectionDraft"):
        tags = Draft202012Validator(definitions[name]["properties"]["context_tags"]["items"])
        assert tags.is_valid("resources:lease_obligation")
        assert not tags.is_valid("resources.lease_obligation")
    tags = Draft202012Validator(definitions["DynamicLifeDirectionDraft"]["properties"]["narrative_tags"]["items"])
    assert tags.is_valid("narrative:community_contact")
    assert not tags.is_valid("community:contact")


@pytest.mark.asyncio
@pytest.mark.parametrize('second_no_op', (False, True))
async def test_disturbance_closure_gets_one_same_author_correction(second_no_op):
    from companion_daemon.world_v2.ledger import WorldLedger
    from test_life_development_runtime import (
        NOW, WORLD_ID, _manifest, _projection_cursor, _runtime, _seed_clock, _SequenceModel,
    )
    from test_world_consequence_producer import _draft

    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    raw = json.dumps(_draft(wake), ensure_ascii=False)
    author = _SequenceModel(model='fixture:world-author', outputs=(raw,
        '{"decision":"no_op"}' if second_no_op else raw))
    runtime, _ = _runtime(ledger=ledger, wake=wake, world_author=author,
        character_interior=_SequenceModel(model='fixture:unused-role', outputs=()))
    manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger)).model_copy(
        update={'outcome_contract': 'world-consequence.2'})
    run = await runtime._world_author_draft(context={}, logical_time=NOW, manifest=manifest,
        wake_event_ref=wake.event_id, occasion_mode='disturbance')
    assert author.calls == 2
    correction = json.loads(author.messages[1][-1]['content'])
    assert correction['validation_failure']['code'] == 'disturbance_missing_consequence'
    assert run.succeeded is second_no_op
    assert len(run.attempts) == 2
    assert ledger.project().ledger_sequence == 1
