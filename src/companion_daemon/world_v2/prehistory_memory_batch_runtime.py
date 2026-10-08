"""Internal batch orchestration of the existing per-source durable Memory workflow."""
from .prehistory_memory_batch import (PURPOSE, MAX_BATCH_SIZE, MAX_BATCH_TEXT_CHARACTERS,
    batch_opportunity, batch_opportunity_from_refs, batch_drafts)
from .prehistory_memory_source import prehistory_memory_binding
from .prehistory_memory_decision import retention_model_result
from .fact_memory_draft import FactMemoryDraftTechnicalFailure
from .schemas import ProjectionCursor


def recovered_batch(runtime, source):
    results = runtime.interior.completed_considerations_for_source(world_id=runtime.ledger.world_id,
        actor_ref=runtime.actor_ref, purpose=PURPOSE, source_ref=source.authority_event_ref)
    found = []
    for result in results:
        projection = runtime.ledger.project_at(result.cursor)
        opportunities = (batch_opportunity_from_refs(world_id=runtime.ledger.world_id, projection=projection,
            source_refs=tuple(result.decision['source_refs']), cursor=result.cursor, attempt_ordinal=i) for i in (1,2,3))
        opportunity = next((op for op in opportunities if op.capability_manifest.payload_hash ==
            result.decision['capability_payload_hash']), None)
        if opportunity is None:
            raise ValueError('recovered batch has an unknown attempt capability')
        if result.opportunity_ref != opportunity.opportunity_ref:
            raise ValueError('recovered batch differs from its original opportunity')
        if source.source_id in batch_drafts(result, opportunity):
            found.append((result, opportunity))
    if len(found) > 1:
        raise ValueError('historical source has multiple completed batch choices')
    return found[0] if found else None


def _source_failures(runtime, source):
    failures = tuple(value for i in range(1, 4) if (value := runtime._recorded(source, i)) is not None)
    if tuple(p.attempt_ordinal for p in failures) != tuple(range(1, len(failures)+1)):
        raise ValueError('historical retry audit has a missing predecessor')
    return failures


async def advance_batch(runtime, *, allow_model_call, batch_size):
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError('historical batch_size must be between 1 and 8')
    projection = runtime.ledger.project()
    pending, recovery_count, status = [], 0, 'idle'
    text_characters = 0
    for row in projection.prehistory_records:
        if row.actor_ref != runtime.actor_ref:
            continue
        source = prehistory_memory_binding(row)
        recorded = runtime._recorded(source)
        if recorded is not None:
            recovery_count += int(runtime._apply(recorded) is not None)
            continue
        failures = _source_failures(runtime, source)
        ordinal = min(len(failures) + 1, 3)
        recovered = recovered_batch(runtime, source)
        if recovered is not None:
            result, opportunity = recovered
            drafts = batch_drafts(result, opportunity)
            payload = runtime._record(source, opportunity, ordinal,
                status='retain' if drafts[source.source_id] is not None else 'no_change', result=result,
                audit=retention_model_result(result, opportunity, source.source_id))
            runtime._apply(payload)
            recovery_count += 1
            continue
        # A setup caller may switch batching on after a paid single-source turn.
        old_opportunity = runtime._opportunity(source)
        old = runtime._recovered(source, old_opportunity)
        if old is not None:
            from .prehistory_memory_decision import retention_draft
            draft = retention_draft(old, old_opportunity)
            payload = runtime._record(source, old_opportunity, ordinal,
                status='retain' if draft is not None else 'no_change', result=old,
                audit=retention_model_result(old, old_opportunity))
            runtime._apply(payload)
            recovery_count += 1
            continue
        if len(failures) == 3:
            status = 'retry_exhausted'
            continue
        if failures and failures[-1].next_retry_at > projection.logical_time:
            status = 'retry_wait'
            continue
        if pending and (row.archive_id != pending[0][0].archive_id or len(pending) >= batch_size
                        or text_characters + len(row.record.statement) > MAX_BATCH_TEXT_CHARACTERS):
            continue
        pending.append((row, source, len(failures)+1))
        text_characters += len(row.record.statement)
    if recovery_count:
        return {'status': 'recovered', 'recovered': recovery_count, 'consider_calls': 0}
    if not pending:
        return {'status': status, 'consider_calls': 0}
    if not allow_model_call:
        return {'status': 'model_call_disabled', 'consider_calls': 0}
    first = pending[0][0]
    located = runtime.ledger.lookup_event_commit(first.accepted_event_ref)
    _, commit = located
    cursor = ProjectionCursor(world_revision=commit.world_revision, deliberation_revision=commit.deliberation_revision,
        ledger_sequence=commit.ledger_sequence)
    pinned = runtime.ledger.project_at(cursor)
    archive = next(a for a in pinned.prehistory_archives if a.manifest.archive_id == first.archive_id)
    opportunity = batch_opportunity(world_id=runtime.ledger.world_id, rows=tuple(r for r, _, _ in pending),
        archive=archive, cursor=cursor, attempt_ordinal=max(ordinal for _, _, ordinal in pending))
    result = await runtime.interior.consider(opportunity)
    try:
        drafts = batch_drafts(result, opportunity)
        failure_code = 'prehistory_batch.item_missing'
    except (FactMemoryDraftTechnicalFailure, ValueError) as exc:
        drafts = {}
        failure_code = getattr(exc, 'failure_code', 'prehistory_batch.invalid_role_lineage')
    counts = {'retained': 0, 'no_change': 0, 'technical_failures': 0}
    for _, source, ordinal in pending:
        if source.source_id not in drafts:
            runtime._record(source, opportunity, ordinal, status='technical_failure', failure_code=failure_code)
            counts['technical_failures'] += 1
            continue
        payload = runtime._record(source, opportunity, ordinal,
            status='retain' if drafts[source.source_id] is not None else 'no_change', result=result,
            audit=retention_model_result(result, opportunity, source.source_id))
        runtime._apply(payload)
        counts['retained' if payload.status == 'retain' else 'no_change'] += 1
    return {'status': 'partial_failure' if counts['technical_failures'] else 'completed',
        'sources': [source.source_id for _, source, _ in pending], 'consider_calls': 1, **counts}
