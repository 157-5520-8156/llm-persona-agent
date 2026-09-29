"""Recover an unversioned epoch.1 serialization choice from immutable history.

An August change reused epoch-continuity.1 while replacing archive Observation
bindings with the genesis operator binding. The first exact committed before-
image can identify which representation originally ran. It supplies no new
Fact values; both candidates derive only from the original genesis payload.
Full event/transition/context validation still runs after this selection.
"""
from __future__ import annotations

import json


def legacy_epoch_fact_binding(genesis, witnesses):
    from .epoch_continuity import ContinuitySnapshot, _genesis_evidence, _rebind_fact
    from .schemas import FactProjection

    if genesis is None or genesis.event_type != 'WorldStarted':
        return 'genesis'
    raw = genesis.payload().get('continuity')
    if not isinstance(raw, dict) or raw.get('continuity_version') != 'epoch-continuity.1':
        return 'genesis'
    snapshot = ContinuitySnapshot.model_validate_json(json.dumps(raw))
    evidence = _genesis_evidence(genesis.event_id, genesis.payload_hash)
    pairs = {
        fact['fact_id']: (
            _rebind_fact(fact, genesis_event_id=genesis.event_id, genesis=evidence,
                         preserve_archive_binding=True),
            _rebind_fact(fact, genesis_event_id=genesis.event_id, genesis=evidence),
        ) for fact in snapshot.facts
    }
    for event in witnesses:
        if event.world_id != genesis.world_id or event.event_type not in {
            'FactCorrected', 'FactWithdrawn', 'FactCorrectionCompensated',
        }:
            continue
        before = event.payload().get('fact_before')
        if not isinstance(before, dict) or before.get('fact_id') not in pairs:
            continue
        before = FactProjection.model_validate_json(json.dumps(before))
        archived, rebound = pairs[before.fact_id]
        if archived == rebound:
            continue
        if before == archived:
            return 'archive'
        if before == rebound:
            return 'genesis'
    # No evidence of the older representation: retain the previously
    # installed replay behavior, never infer a mode from a mutable head cache.
    return 'genesis'
