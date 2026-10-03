from copy import deepcopy
import json
import os

from companion_daemon.world_v2.character_interior.inbound_context_presentation import (
    cache_ordered_inbound_material,
)
from companion_daemon.world_v2.reference_wire import expand_reference_view, prepare_reference_view


def _packet(text, cursor):
    ref = 'event:character:' + 'a' * 64
    return {
        'request': {'call_id': 'call:' + 'b' * 64 + str(cursor)},
        'inner_life_snapshot': {
            'cursor': {'ledger_sequence': cursor},
            'materials': {
                'recent_dialogue': [{'text': text, 'source_ref': 'event:dialogue:' + 'c' * 64 + str(cursor)}],
                'stable_self': [{'text': '她的性格与身世', 'source_ref': ref}],
                'routine_background': {'text': '不强制执行的生活习惯', 'null': None},
                'moments_i_can_share': {'items': [
                    {'candidate_id': 'photo:' + 'd' * 64, 'photo_in_hand': False,
                     'hold_reason': 'expired', 'source_ref': ref},
                ]},
            },
            'source_refs': [ref],
        },
        'expression_hard_boundaries': {'source_refs': [ref]},
        'current_trigger_message': {'text': text},
        'recall_available': True,
    }


def test_ordering_keeps_values_permissions_and_source_decoding_exact():
    packet = _packet('回忆可以有感受，但来源不可改', 12)
    original = deepcopy(packet)
    ordered = cache_ordered_inbound_material(packet)
    assert ordered == packet == original
    view, bindings = prepare_reference_view(ordered)
    assert expand_reference_view(view, bindings) == original
    candidate = view['inner_life_snapshot']['materials']['moments_i_can_share']['items'][0]
    assert candidate['photo_in_hand'] is False and candidate['hold_reason'] == 'expired'


def test_changing_dialogue_does_not_renumber_stable_source_or_break_its_prefix():
    views = [prepare_reference_view(cache_ordered_inbound_material(_packet(text, cursor)))[0]
             for text, cursor in [('昨天怎么样', 12), ('以前的生日呢', 13)]]
    stable = [v['inner_life_snapshot']['materials']['stable_self'] for v in views]
    assert stable[0] == stable[1]
    wire = [json.dumps(v, ensure_ascii=False, separators=(',', ':')) for v in views]
    prefix = os.path.commonprefix(wire)
    assert '她的性格与身世' in prefix
    assert 'expired' in prefix
    assert '昨天怎么样' not in prefix
    assert '以前的生日呢' not in prefix


def test_media_rows_roundtrip_preserves_missing_null_and_literal_selectors():
    import pytest
    from companion_daemon.world_v2.character_interior.inbound_context_presentation import (
        pack_literal_rows, unpack_literal_rows,
    )

    items = [{
        'candidate_id': 'candidate:' + str(index), 'source_ref': 'source:' + str(index),
        'family': 'character_media', 'taxonomy': 'character_media:selfie',
        'privacy_class': 'private', 'photo_in_hand': False, 'hold_reason': 'expired',
        **({'location_ref': None} if index % 2 else {}),
    } for index in range(20)]
    items[0]['hold_reason'] = '__absent_cell__'
    packed = pack_literal_rows(items)
    assert isinstance(packed, dict)
    assert unpack_literal_rows(packed) == items
    assert len(json.dumps(packed)) < len(json.dumps(items))
    corrupted = deepcopy(packed)
    corrupted['stable_rows'][0].pop()
    with pytest.raises(ValueError):
        unpack_literal_rows(corrupted)


def test_reference_codec_understands_media_row_source_columns():
    from companion_daemon.world_v2.character_interior.inbound_context_presentation import (
        compact_inbound_media_rows, unpack_literal_rows,
    )
    original = _packet('试试照片', 12)
    items = original['inner_life_snapshot']['materials']['moments_i_can_share']['items']
    items *= 20
    packed = compact_inbound_media_rows(original)
    view, bindings = prepare_reference_view(cache_ordered_inbound_material(packed))
    expanded = expand_reference_view(view, bindings)
    album = expanded['inner_life_snapshot']['materials']['moments_i_can_share']
    album['items'] = unpack_literal_rows(album['items'])
    assert expanded == original


def test_flat_memory_rows_preserve_nested_keys_absent_null_arrays_and_references():
    from companion_daemon.world_v2.character_interior.inbound_context_presentation import (
        pack_flat_literal_rows, unpack_flat_literal_rows,
    )

    source = "source:" + "e" * 64
    items = [{"token": f"m{i}", "candidate": {"source_ref": source, "values": {
        "salience": {"emotional_residue_bp": 5000}, "nullable": None, "empty": {},
        "a/b~c": {"": "__absent_cell__"}, **({"optional": False} if i % 2 else {}),
    }}, "reading": {"source_excerpts": [{"source_ref": source, "text": "原文不能省略"}]}}
        for i in range(12)]
    packed = pack_flat_literal_rows(items)
    assert isinstance(packed, dict)
    assert unpack_flat_literal_rows(packed) == items
    view, bindings = prepare_reference_view({"memories": packed})
    restored = expand_reference_view(view, bindings)
    assert unpack_flat_literal_rows(restored["memories"]) == items


def test_flat_memory_rows_reject_colliding_or_noncanonical_paths():
    import pytest
    from companion_daemon.world_v2.character_interior.inbound_context_presentation import unpack_flat_literal_rows

    for columns, row in [(["/a", "/a/b"], [1, 2]), (["/bad~2"], [1])]:
        packet = {"contract": "literal-flat-row-table.1", "columns": columns,
                  "stable_rows": [row], "absent_cell": "__absent_cell__"}
        with pytest.raises(ValueError):
            unpack_flat_literal_rows(packet)
