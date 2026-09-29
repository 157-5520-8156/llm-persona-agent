"""Storage round trips cannot create, truncate or expand review authority."""
import base64
import hashlib
import json
import zlib

import pytest

from companion_daemon.world_v2 import visible_review_evidence_storage as storage


def raw_evidence():
    return storage._canonical({
        'contract': 'visible-source-runtime-evidence.3',
        'requirement_json': storage._canonical({'review_protocol': 'visible-independent-review.5'}),
        'receipt': {'exact_original': '证据\\"\n' * 100_000},
    })


def test_large_utf8_evidence_restores_exact_original_bytes():
    raw = raw_evidence()
    assert storage.MAX_STORED_BYTES < len(raw.encode()) < storage.MAX_DECODED_BYTES
    packed = storage.store_review_evidence(raw)
    assert len(packed.encode()) < storage.MAX_STORED_BYTES
    assert storage._canonical(storage.read_review_evidence(packed)) == raw
    small = '{"contract":"legacy"}'
    assert storage.store_review_evidence(small) == small
    assert storage.read_review_evidence(small) == json.loads(small)


@pytest.mark.parametrize('protocol', ['visible-grounded-review.1', 'visible-grounded-review.2'])
def test_grounded_review_retains_large_subjective_history_without_losing_receipt(protocol):
    raw = raw_evidence().replace('visible-independent-review.5', protocol)
    stored = storage.store_review_evidence(raw)
    assert len(stored.encode()) < storage.MAX_STORED_BYTES
    assert storage._canonical(storage.read_review_evidence(stored)) == raw


@pytest.mark.parametrize('fault', ['digest', 'size', 'oversize', 'bool_size', 'base64', 'truncated', 'trailing_stream', 'extra_field', 'expansion'])
def test_corrupt_or_unbounded_storage_cannot_be_decoded(fault):
    value = json.loads(storage.store_review_evidence(raw_evidence()))
    if fault == 'digest':
        value['decoded_sha256'] = '0' * 64
    elif fault == 'size':
        value['decoded_bytes'] -= 1
    elif fault == 'oversize':
        value['decoded_bytes'] = storage.MAX_DECODED_BYTES + 1
    elif fault == 'bool_size':
        value['decoded_bytes'] = True
    elif fault == 'base64':
        value['data_base64'] = '!invalid!'
    elif fault == 'extra_field':
        value['allow'] = True
    elif fault == 'expansion':
        value['decoded_bytes'] = 20
    else:
        packed = base64.b64decode(value['data_base64'])
        packed = packed[:-1] if fault == 'truncated' else packed + zlib.compress(b'{}')
        value['data_base64'] = base64.b64encode(packed).decode()
    with pytest.raises(ValueError):
        storage.read_review_evidence(storage._canonical(value))


def test_repacked_legacy_storage_cannot_acquire_new_size_allowance():
    original = raw_evidence().replace('visible-independent-review.5', 'visible-independent-review.4')
    with pytest.raises(ValueError, match='pinned protocol'):
        storage.store_review_evidence(original)
    value = json.loads(storage.store_review_evidence(raw_evidence()))
    # A matching storage hash is not proof that the original protocol allowed it.
    value.update(decoded_bytes=len(original.encode()), decoded_sha256=hashlib.sha256(original.encode()).hexdigest(),
                 data_base64=base64.b64encode(zlib.compress(original.encode())).decode())
    with pytest.raises(ValueError, match='pinned protocol'):
        storage.read_review_evidence(storage._canonical(value))
    with pytest.raises(ValueError, match='aggregate bound'):
        storage.store_review_evidence('x' * (storage.MAX_DECODED_BYTES + 1))
