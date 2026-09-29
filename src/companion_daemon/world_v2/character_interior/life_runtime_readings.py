"""Narrow original-capability readings, checked again by the World authority."""
from datetime import datetime

from ..schemas import ExecutionReceipt
from .life_source_origin import canonical, digest


def runtime_readings(*, view, snapshot):
    payload = view.author_payload()
    manifest = payload.get('capability_manifest', {})
    if manifest.get('capability_kind') != 'world_stimulus_appraisal':
        return []
    capability = manifest.get('payload', {})
    if manifest.get('payload_hash') != 'sha256:' + digest(canonical(capability)):
        raise ValueError('Life runtime reading lacks the original capability binding')
    source = capability.get('source_event', {})
    if source.get('event_type') != 'ExecutionReceiptRecorded':
        return []
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
        return []
    at = datetime.fromisoformat(source['logical_time'])
    if at > snapshot.logical_time:
        raise ValueError('Life receipt is newer than the original pin')
    receipt = ExecutionReceipt.model_validate_json(canonical(source['payload']['receipt']), strict=True)
    readings = []

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
