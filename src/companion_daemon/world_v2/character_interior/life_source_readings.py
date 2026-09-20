"""Exact, presented field readings for a bounded set of Life source families.

Code restricts source use and byte identity; it never classifies a candidate or
judges entailment. Unknown families and unshown fields receive no default grant.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from ..present_prompt import appraisal_material_rows, recent_dialogue_material_entries
from ..visible_prehistory_readings import prehistory_field_permissions
from ..visible_source_closure_protocol import _eligible_reference, _review_material
from ..visible_source_subject_authority import source_subject_permissions
from ..visible_source_witness_experiment import _reading, _relative_pointer_choices
from ..visible_subjective_source import subjective_direct_paths
from .life_source_origin import canonical, digest
from .life_source_state_readings import CONTRACT as STATE_CONTRACT, lifecycle_state_reading
from .life_affect_history_readings import affect_history_reading
from .life_source_view import LifeSourceView
from .life_biographical_readings import biographical_reading
from .life_fact_readings import EXACT_VALUE_REVIEW_CONTRACTS, fact_value_reading

CONTRACT = "life-source-readings.3"
EXACT_VALUE_CONTRACT = "life-source-readings.4"


def _fields(row):
    """Explicit source-family contracts, with no general scalar fallback."""
    material = row['review_material']
    if material.get('lane') == 'recent_dialogue' and material.get('authority') == 'counterpart_report_only':
        # Life has no implicit current-counterpart binding. The exact recorded
        # speaker can still own a report; this grants no objective event truth.
        from ..recent_dialogue import RecentDialogueItem
        if not _review_material(material)[1]:
            return None
        dialogue = RecentDialogueItem.model_validate_json(canonical(material['item']['value']), strict=True)
        refs = {material['item']['item_ref'], *(claim.authority_event_ref for claim in dialogue.source_claims)}
        if dialogue.speaker != 'counterpart' or dialogue.speaker_ref != row.get('support_subject_ref') or row['source_ref'] not in refs:
            return None
        return 'counterpart_report', ('recent_dialogue',), {'/item/value/text': [['utterance_record', 'source_owner'], ['report_uptake', 'source_owner']]}
    if not _eligible_reference(row):
        return None
    historical = prehistory_field_permissions(row)
    if historical is not None:
        return 'retained_prehistory', ('remembered_material',), historical
    owner = row.get('support_subject_role') or ('source_owner' if row.get('support_subject_ref') else None)
    if isinstance(row.get('settled_life_support'), dict):
        paths = [
            '/item/value/content/world_consequence/environment/text',
            '/item/value/content/world_consequence/authorized_attempt_result/text',
        ]
        return 'settled_life', ('recent_self_experiences', 'week_diary'), source_subject_permissions(row=row, pointers=paths)
    if isinstance(row.get('activity_support'), dict):
        scopes = {'planned': 'planned_activities', 'active': 'current_activities',
                  'in_progress': 'current_activities', 'completed': 'recently_ended_activities'}
        status = row['activity_support']['status']
        if status not in scopes:
            return None
        fields = {'/item/value/accepted_intention/text': [['accepted_intention', owner]]}
        if status in {'active', 'in_progress', 'completed'}:
            for path in ('/item/value/status', '/item/value/started_at', '/item/value/ended_at'):
                fields[path] = [['activity_lifecycle', owner]]
        return 'activity', (scopes[status],), fields
    if material.get('lane') == 'recent_dialogue' and owner is not None:
        if material.get('authority') == 'companion_expression_record':
            return 'companion_utterance', ('recent_dialogue',), {'/item/value/text': [['utterance_record', owner]]}
    if 'subjective_history_support' in row:
        paths = subjective_direct_paths(row, _relative_pointer_choices([material]))
        scope = {'appraisals': 'appraisals', 'affect_episodes': 'affect'}.get(material.get('lane'))
        if scope is not None:
            return 'subjective_history', (scope,), source_subject_permissions(row=row, pointers=paths)
    # Fact values use the separate hash-bound quotation reader, never this
    # scalar path's unconditional field permission.
    return None


def _ref_items(value, source_ref):
    if isinstance(value, dict):
        if value.get('source_ref') == source_ref:
            yield value
            return
        for child in value.values():
            yield from _ref_items(child, source_ref)
    elif isinstance(value, list):
        for child in value:
            yield from _ref_items(child, source_ref)


def _presented_items(rendered, *, scope, source_ref):
    material = rendered['materials'].get(scope)
    if scope == 'recent_dialogue':
        material = recent_dialogue_material_entries(material)
    if scope == 'appraisals' and isinstance(material, dict):
        columns = material.get('columns')
        if not isinstance(columns, list) or 'ref' not in columns or 'readings' not in columns:
            return []
        ref_index, reading_index = columns.index('ref'), columns.index('readings')
        # Invert only the known compact meaning column, preserving order.
        return [{'hypotheses': [{'meaning': reading[0]} for reading in row[reading_index]
                                if isinstance(reading, list) and reading and isinstance(reading[0], str)]}
                for row in appraisal_material_rows(material)
                if len(row) == len(columns) and row[ref_index] == source_ref
                and isinstance(row[reading_index], list)]
    return list(_ref_items(material, source_ref))


def _shown_exact_field(shown, pointer, expected, family):
    if not isinstance(shown, dict):
        return False
    if family == 'settled_life' and 'world_consequence' in shown:
        # week_diary removes the outer content wrapper, not source semantics.
        shown = {'content': {'world_consequence': shown['world_consequence']}}
    try:
        value, numeric = _reading({'item': {'value': shown}}, pointer)
    except ValueError:
        return False
    return not numeric and value == expected


@dataclass(frozen=True)
class PreparedLifeSourceReadings:
    payload_json: str

    def as_dict(self):
        return json.loads(self.payload_json)

    def verify(self, *, view: LifeSourceView, snapshot):
        expected = prepare_life_source_readings(view=view, snapshot=snapshot)
        if self.payload_json != expected.payload_json:
            raise ValueError('Life field readings differ from the pinned source/view compilation')
        return expected

    def require_reading(self, *, reading_id: str, claim_scope: str, subject_role: str, view: LifeSourceView, snapshot):
        checked = self.verify(view=view, snapshot=snapshot)
        reading = next((item for item in checked.as_dict()['readings'] if item['reading_id'] == reading_id), None)
        if reading is None or [claim_scope, subject_role] not in reading['permissions']:
            raise ValueError('Life reading selection exceeds its source field permission')
        # Returning an exact source field never establishes candidate entailment.
        return reading

    def require_fact_value(self, *, reading_id: str, quoted_value: str, claim_scope: str,
                           subject_ref: str, view: LifeSourceView, snapshot):
        from ..fact_observation_value import FactObservationValueBinding

        checked = self.verify(view=view, snapshot=snapshot)
        reading = next((item for item in checked.as_dict()['readings'] if item['reading_id'] == reading_id), None)
        if (reading is None or reading['source_family'] != 'accepted_fact_value'
            or subject_ref != reading['source_owner_ref']
            or [claim_scope, 'source_owner'] not in reading['value_selection_permissions']):
            raise ValueError('Life Fact selection exceeds its predicate/subject/status permission')
        binding = FactObservationValueBinding.model_validate_json(canonical(reading['value_binding']), strict=True)
        value = binding.select(source_excerpt=reading['value'], quoted_value=quoted_value)
        # The candidate's semantic relation to this predicate still needs review.
        return {'reading_id': reading_id, 'quoted_value': value, 'claim_scope': claim_scope,
                'subject_ref': subject_ref, 'fact_context': reading['fact_context'],
                **({'accepted_value': value, 'observation_event_ref': reading['observation_event_ref']}
                   if 'accepted_value' in reading else {'observation_context': reading['value']}),
                'write_authority': False, 'semantic_coverage': 'not_assessed'}


def prepare_life_source_readings(*, view: LifeSourceView, snapshot) -> PreparedLifeSourceReadings:
    view = view.verify_snapshot(snapshot)
    table = json.loads(view.source_table_json)
    rendered = json.loads(json.loads(view.messages_json)[1]['content'])['inner_life_snapshot']
    visible = {(item['source_ref'], item['scope']) for item in rendered.get('source_inventory', ())}
    readings, excluded, identities = [], [], {}
    exact_value_display = view.review_contract in EXACT_VALUE_REVIEW_CONTRACTS
    for source in table['source_references']:
        material = table['source_materials'][source['material_index']]['material']
        row = {**source, 'review_material': material}
        structured_reader = (
            affect_history_reading if view.review_contract == STATE_CONTRACT and material.get("lane") == "affect_episodes" else
            lifecycle_state_reading if view.review_contract == STATE_CONTRACT and material.get("item", {}).get("value", {}).get("context_kind") == "activity_lifecycle_state" else
            biographical_reading if material.get('kind') == 'biographical_coordinate' else
            fact_value_reading if material.get('lane') == 'relevant_facts' else None
        )
        if structured_reader is not None:
            descriptor, reason = structured_reader(row, rendered=rendered,
                **({'exact_value_display': exact_value_display} if structured_reader is fact_value_reading else
                   {'snapshot': snapshot} if structured_reader in {lifecycle_state_reading, affect_history_reading} else {}))
            if descriptor is None:
                excluded.append({'source_ref_index': source['source_ref_index'], 'reason': reason})
                continue
            descriptor.update(material_index=source['material_index'],
                              material_identity=source['material_identity'],
                              source_owner_ref=descriptor.get('source_owner_ref', source.get('support_subject_ref')))
            identity = canonical(descriptor)
            if identity in identities:
                identities[identity]['source_ref_indexes'].append(source['source_ref_index'])
                continue
            reading = {'reading_id': 'life-reading:sha256:' + digest(identity), **descriptor,
                       'source_ref_indexes': [source['source_ref_index']]}
            identities[identity] = reading
            readings.append(reading)
            continue
        specification = _fields(row)
        if specification is None:
            excluded.append({'source_ref_index': source['source_ref_index'], 'reason': 'no_qualified_field_reader'})
            continue
        family, scopes, fields = specification
        item_ref = material.get('item', {}).get('item_ref')
        presented = [(scope, item) for scope in scopes if (item_ref, scope) in visible
                     for item in _presented_items(rendered, scope=scope, source_ref=item_ref)]
        if not presented:
            excluded.append({'source_ref_index': source['source_ref_index'], 'reason': 'source_not_in_presented_family'})
            continue
        for pointer, permissions in fields.items():
            if not permissions:
                continue
            try:
                value, numeric = _reading(material, pointer)
            except ValueError:
                continue  # Optional source field is absent, not an invented empty reading.
            if numeric or not value:
                continue
            shown_scopes = sorted({scope for scope, shown in presented if _shown_exact_field(shown, pointer, value, family)})
            if not shown_scopes:
                excluded.append({'source_ref_index': source['source_ref_index'], 'pointer': pointer,
                                 'reason': 'exact_field_not_presented'})
                continue
            descriptor = {'source_family': family, 'material_index': source['material_index'],
                          'material_identity': source['material_identity'], 'item_ref': item_ref,
                          'pointer': pointer, 'value': value, 'shown_scopes': shown_scopes,
                          'source_owner_ref': source.get('support_subject_ref'), 'permissions': permissions}
            identity = canonical(descriptor)
            if identity in identities:
                identities[identity]['source_ref_indexes'].append(source['source_ref_index'])
                continue
            reading = {'reading_id': 'life-reading:sha256:' + digest(identity), **descriptor,
                       'source_ref_indexes': [source['source_ref_index']]}
            identities[identity] = reading
            readings.append(reading)
    return PreparedLifeSourceReadings(canonical({
        'contract': EXACT_VALUE_CONTRACT if exact_value_display else CONTRACT,
        'source_view_sha256': digest(view.model_dump_json()),
        'snapshot_hash': snapshot.snapshot_hash, 'readings': readings, 'excluded': excluded,
        'write_authority': False, 'semantic_coverage': 'not_assessed',
        'complete_source_coverage': False,
    }))
