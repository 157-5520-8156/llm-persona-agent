"""Read accepted, delivered text only; never decode opaque payloads or drafts."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DashboardExpressionReading:
    status: str
    text: str | None = None
    occurred_at: datetime | None = None
    beat_count: int = 0
    delivered_count: int = 0
    shown_count: int = 0


def read_dashboard_expression(projection, plan):
    manifests = [m for m in projection.expression_plan_manifests
                 if m.plan_id == plan.plan_id and m.acceptance_id == plan.acceptance_id
                 and m.proposal_id == plan.proposal_id
                 and m.expression_change_id == plan.expression_change_id]
    if len(manifests) != 1:
        return DashboardExpressionReading('unavailable')
    manifest = manifests[0]
    event = next((e for e in projection.committed_world_event_refs
                  if e.event_id == manifest.acceptance_event_ref
                  and e.event_type == 'AcceptanceRecorded'
                  and e.payload_hash == manifest.acceptance_event_payload_hash
                  and e.world_revision == manifest.recorded_at_world_revision), None)
    if event is None:
        return DashboardExpressionReading('unavailable')
    texts, delivered = [], 0
    for beat in manifest.beats:
        action = next((a for a in projection.actions if a.action_id == beat.action.action_id), None)
        if action is None or action.state != 'delivered':
            continue
        delivered += 1
        if (beat.privacy_class == 'withhold' or beat.storage_kind != 'inline_text'
            or beat.content_type != 'text/plain' or not beat.text):
            continue
        matches = [p for p in projection.stored_message_payloads
                   if p.acceptance_id == plan.acceptance_id and p.proposal_id == plan.proposal_id
                   and p.payload_ref == beat.payload_ref and p.payload_hash == beat.payload_hash
                   and p.content_type == beat.content_type and p.text == beat.text]
        if len(matches) == 1:
            texts.append(beat.text)
    status = ('read' if len(texts) == len(manifest.beats) else 'partial' if texts
              else 'not_delivered' if not delivered else 'not_displayable')
    return DashboardExpressionReading(status, '\n'.join(texts) or None, event.logical_time,
                                      len(manifest.beats), delivered, len(texts))


def expectation_display_status(projection, manifest):
    expectation = manifest.response_expectation
    matching = [a for a in projection.response_expectation_assessments
                if a.source_plan_id == manifest.plan_id
                and a.source_acceptance_event_ref == manifest.acceptance_event_ref]
    latest = max(matching, key=lambda a: (a.assessed_at, a.world_revision), default=None)
    if latest is not None and latest.status in {'fulfilled', 'superseded'}:
        return latest.status
    window = window_display_status(projection.logical_time, expectation)
    return latest.status if latest is not None and window == 'open' else window


def window_display_status(logical_time, window):
    if logical_time is None:
        return 'unknown'
    if logical_time >= window.expires_at:
        return 'expired'
    return 'scheduled' if logical_time < window.not_before else 'open'
