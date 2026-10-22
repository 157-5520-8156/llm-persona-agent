"""Narrow original-capability readings, checked again by the World authority."""
from datetime import datetime
import json

from ..schemas import ExecutionReceipt
from .life_source_origin import canonical, digest


def runtime_readings(*, view, snapshot, include_pinned_context=False):
    payload = view.author_payload()
    manifest = payload.get('capability_manifest', {})
    if manifest.get('capability_kind') != 'world_stimulus_appraisal':
        return []
    capability = manifest.get('payload', {})
    if manifest.get('payload_hash') != 'sha256:' + digest(canonical(capability)):
        raise ValueError('Life runtime reading lacks the original capability binding')
    readings = _pinned_context_readings(view=view, snapshot=snapshot) if include_pinned_context else []
    source = capability.get('source_event', {})
    if source.get('event_type') != 'ExecutionReceiptRecorded':
        return readings
    ref, sha = source.get('event_id'), source.get('payload_hash')
    if not isinstance(ref, str) or sha != digest(canonical(source.get('payload'))):
        raise ValueError('Life receipt differs from the original event bytes')
    evidence = snapshot.materials.get('capability_evidence', [])
    bound = any(e.get('source_ref') == ref and e.get('event_type') == source['event_type']
                and e.get('payload_hash') == sha and 0 < e.get('source_world_revision', 0) <= snapshot.cursor.world_revision
                for e in evidence)
    bound |= any(b.ref == ref and b.immutable_hash == sha
                 and b.authority_type == 'ExecutionReceiptRecorded'
                 and b.source_world_revision <= snapshot.cursor.world_revision
                 for item in snapshot.source_inventory for b in item.authority_bindings)
    if not bound:
        return readings
    at = datetime.fromisoformat(source['logical_time'])
    if at > snapshot.logical_time:
        raise ValueError('Life receipt is newer than the original pin')
    receipt = ExecutionReceipt.model_validate_json(canonical(source['payload']['receipt']), strict=True)

    def add(family, pointer, value, scope):
        descriptor = {'source_family': family, 'source_owner_ref': snapshot.actor_ref,
                      'item_ref': ref, 'pointer': pointer, 'value': value,
                      'shown_scopes': ['capability_manifest'],
                      'permissions': [['external_fact', 'companion']],
                      'scope': scope, 'capability_payload_hash': manifest['payload_hash']}
        readings.append({'reading_id': 'life-reading:sha256:' + digest(canonical(descriptor)), **descriptor})

    add('delivery_receipt', '/capability_manifest/payload/source_event/payload/receipt', {
        'receipt_kind': receipt.receipt_kind, 'observed_state': receipt.observed_state,
        'is_terminal': receipt.is_terminal, 'recorded_at': source['logical_time'],
    }, 'Transport receipt only. Delivered does not prove read, understood, replied, remembered or cared about.')
    gap = capability.get('silence_observation')
    if isinstance(gap, dict) and gap.get('status') == 'available':
        if (gap.get('contract') != 'silence-observation.1' or ref not in gap.get('source_event_refs', [])
            or datetime.fromisoformat(gap['observed_at']) != snapshot.logical_time):
            raise ValueError('Life silence chronology differs from its original pin')
        # The acceptance authority independently recompiles this exact object
        # from the pinned ledger before accepting any proposed inner effect.
        add('channel_reply_gap', '/capability_manifest/payload/silence_observation',
            {k: v for k, v in gap.items() if k not in {'instruction', 'source_event_refs'}},
            'Recorded channel chronology only; no read status, motive, emotional interpretation or reply obligation.')
    return readings


def _pinned_context_readings(*, view, snapshot):
    """Read the original trusted pin and exact already-presented memory fields.

    The MemoryCandidate controls retrieval only. Its typed Experience excerpt
    was separately verified by MemoryRetrievalCompiler against the original
    World/response/descriptor closure; no summary or retention rationale grants
    a new fact permission here.
    """
    from ..memory_retrieval import MemoryRetrievalItem

    snapshot.life_source_origin.verify_snapshot(snapshot)
    capsule = snapshot.life_source_origin.capsule()
    rendered = view.host_inner_life(snapshot)
    readings = []

    def add(family, item_ref, pointer, value, permissions, scope, identity, owner=None):
        descriptor = {'source_family': family, 'source_owner_ref': owner, 'item_ref': item_ref,
                      'pointer': pointer, 'value': value, 'permissions': permissions,
                      'shown_scopes': ['cursor' if family == 'current_world_clock' else 'remembered_material'],
                      'scope': scope, 'material_identity': identity}
        readings.append({'reading_id': 'life-reading:sha256:' + digest(canonical(descriptor)), **descriptor})

    at = rendered.get('cursor', {}).get('logical_time')
    if capsule.logical_time is not None:
        if not isinstance(at, str) or datetime.fromisoformat(at) != snapshot.logical_time:
            raise ValueError('Life clock differs from its exact original Capsule pin')
        add('current_world_clock', snapshot.snapshot_id, '/cursor/logical_time', {'logical_time': at},
            [['external_fact', 'general']],
            'Pinned World time only; no daylight, location, perception, action or outcome authority.',
            capsule.snapshot_hash)

    selected = capsule.active_memory_candidates
    if selected.availability != 'available':
        return readings
    shown = rendered.get('materials', {}).get('remembered_material', ())
    for item in selected.items:
        raw = json.loads(item.payload_json)
        if 'source_excerpts' not in raw:
            continue
        memory = MemoryRetrievalItem.model_validate_json(item.payload_json, strict=True)
        visible = [m for m in shown if m.get('source_ref') == memory.candidate_id]
        if not visible:
            continue
        if (len(visible) != 1 or item.item_ref != memory.candidate_id
                or item.privacy_class == 'withhold' or memory.privacy_ceiling == 'withhold'):
            raise ValueError('Life memory differs from the original selected identity/privacy')
        bindings = {(b.ref, b.authority_type, b.source_world_revision, b.immutable_hash)
                    for b in item.source_bindings if b.source_kind == 'committed_event'}
        displayed = visible[0].get('source_excerpts', ())
        for index, source in enumerate(memory.source_excerpts):
            if source.world_consequence is None:
                continue
            if (source.source_kind != 'experience' or source.character_response is None
                    or source.character_response.actor_ref != snapshot.actor_ref
                    or source.authority_world_revision > capsule.world_revision
                    or (source.authority_event_ref, 'ExperienceCommitted', source.authority_world_revision,
                        source.authority_payload_hash) not in bindings):
                raise ValueError('Life remembered Experience lacks its original actor/accepted event')
            visible_source = [x for x in displayed if x.get('source_id') == source.source_id
                              and x.get('excerpt_ref') == source.excerpt_ref
                              and x.get('authority_event_ref') == source.authority_event_ref
                              and x.get('authority_world_revision') == source.authority_world_revision]
            if len(visible_source) != 1:
                continue  # A selected but unshown source never acquires a field permission.
            identity = digest(canonical({'selected_value_hash': item.value_hash,
                'source': source.model_dump(mode='json')}))
            world = source.world_consequence
            for family, field, value, permissions, scope in (
                ('remembered_world_environment', 'world_consequence/environment', world.environment,
                 [['environment', 'general']],
                 'Past accepted external environment only; no current persistence or personal perception/action.'),
                ('remembered_attempt_result', 'world_consequence/authorized_attempt_result', world.authorized_attempt_result,
                 [['external_fact', 'companion']],
                 'Past result of this exact bound attempt only; no new action or current completion.'),
                ('remembered_character_response', 'character_response', source.character_response,
                 [['subjective_history', 'companion']],
                 'Original private interpretation record only; no external fact or current feeling authority.'),
            ):
                if value is None or value.truncated:
                    continue
                if (family == 'remembered_world_environment' and value.epistemic_scope != 'settled_world_environment'
                        or family == 'remembered_attempt_result' and (value.epistemic_scope != 'settled_result_of_bound_attempt'
                            or value.execution_binding.actor_ref != snapshot.actor_ref)):
                    raise ValueError('Life memory field is not a settled source for this actor')
                visible_value = visible_source[0]
                for key in field.split('/'):
                    visible_value = visible_value.get(key) if isinstance(visible_value, dict) else None
                exact = value.model_dump(mode='json')
                if visible_value != exact:
                    continue
                add(family, memory.candidate_id, f'/source_excerpts/{index}/{field}', exact,
                    permissions, scope, identity,
                    snapshot.actor_ref if family != 'remembered_world_environment' else None)
    return readings
