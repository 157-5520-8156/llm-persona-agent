"""Selected accepted inner state proves subjective history, never its beliefs.

The trusted Capsule producer supplies ownership in the World companion's
private context. Appraisal.subject_ref denotes what she interpreted, not whose
mind the record describes. This reader does not classify candidate language.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json

from .context_capsule import ResolvedSourceBinding, source_bindings_hash
from .schemas import AffectEpisodeProjection, AppraisalProjection

CONTRACT = 'visible-subjective-history-source.1'
AUTHORITY = 'accepted_subjective_history_not_external_fact'
_TYPES = {
    'appraisals': (AppraisalProjection, 'appraisal_id', {'AppraisalAccepted', 'AppraisalSuperseded'}),
    'affect_episodes': (AffectEpisodeProjection, 'episode_id', {'AffectEpisodeOpened', 'AffectEpisodeSuperseded'}),
}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def subjective_source_support(material: dict, source_ref: str) -> tuple[str, dict] | None:
    """Check complete selected values and acceptance bindings before any use."""
    lane = material.get('lane')
    if (lane not in _TYPES or material.get('kind') != 'pinned_context_item'
            or material.get('authority') != AUTHORITY or material.get('availability') != 'available'
            or material.get('privacy_class') != 'private' or source_ref not in material.get('source_refs', ())):
        return None
    item = material.get('item')
    scope = material.get('scope')
    if not isinstance(item, dict) or not isinstance(scope, dict):
        return None
    actor = material.get('actor_ref')
    if (not isinstance(actor, str) or not actor or scope.get('contract') != CONTRACT
            or scope.get('owner_actor_ref') != actor or scope.get('owner_basis') != 'pinned_companion_private_context'
            or item.get('privacy_class') != 'private' or item.get('value_hash') != _hash(item.get('value'))):
        return None
    model, id_field, event_types = _TYPES[lane]
    try:
        value = model.model_validate_json(json.dumps(item.get('value')), strict=True)
        bindings = tuple(ResolvedSourceBinding.model_validate(b) for b in item.get('source_bindings', ()))
        logical_at = datetime.fromisoformat(scope['logical_at'])
    except (KeyError, TypeError, ValueError):
        return None
    if (logical_at.tzinfo is None or value.status != 'active'
            or item.get('item_ref') != getattr(value, id_field)
            or len(bindings) != 1 or source_bindings_hash(bindings) != item.get('source_hash')):
        return None
    binding = bindings[0]
    if (binding.source_kind != 'committed_event' or binding.ref != value.origin.accepted_event_ref
            or binding.authority_type not in event_types
            or type(scope.get('world_revision')) is not int
            or binding.source_world_revision > scope['world_revision']
            or source_ref not in {getattr(value, id_field), binding.ref}):
        return None
    if isinstance(value, AppraisalProjection):
        observed_at = value.accepted_at
    else:
        if value.privacy_class != 'private':
            return None
        observed_at = value.updated_at
    if observed_at > logical_at:
        return None
    return actor, {
        'contract': CONTRACT, 'scope': 'subjective_history_only', 'lane': lane,
        'source_event_type': binding.authority_type, 'logical_at': logical_at.isoformat(),
        'recorded_at': observed_at.isoformat(),
    }


def subjective_direct_paths(row: dict, paths: list[str]) -> list[str]:
    """Only interpreted meanings/dimensions are selectors; full context stays."""
    if row['review_material']['lane'] == 'appraisals':
        return [p for p in paths if p.startswith('/item/value/hypotheses/') and p.endswith('/meaning')]
    return [p for p in paths if p.startswith('/item/value/components/') and p.endswith('/dimension')]
