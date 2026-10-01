"""Read optimizations retain live source checks and the addressed SQL plan."""
import pytest

from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.schemas import ProjectionCursor


def test_receipt_lookup_uses_action_index_and_does_not_scan_world_history(tmp_path):
    ledger=SQLiteWorldLedger(path=tmp_path/"world.sqlite",world_id="world:receipt-index")
    plan=ledger._connection.execute("""
        EXPLAIN QUERY PLAN SELECT event_id
        FROM world_v2_events INDEXED BY world_v2_events_receipt_action_lookup
        WHERE world_id = ? AND ledger_sequence <= ?
          AND json_extract(event_json, '$.event_type') = 'ExecutionReceiptRecorded'
          AND json_extract(json_extract(event_json, '$.payload_json'), '$.receipt.action_id') IN (?,?)
        ORDER BY ledger_sequence
    """,(ledger.world_id,500,"action:one","action:two")).fetchall()
    assert any("world_v2_events_receipt_action_lookup" in str(row[3]) for row in plan)
    assert ledger.execution_receipt_events_for_actions(action_ids=frozenset(),
        cursor=ProjectionCursor(world_revision=0,deliberation_revision=0,ledger_sequence=0))==()
    ledger.close()


@pytest.mark.asyncio
async def test_warm_composite_parsing_does_not_keep_a_withdrawn_descriptor_readable(tmp_path,monkeypatch):
    from test_character_life_experience_runtime import _accepted_response,_cursor,WORLD,ACTOR
    from companion_daemon.world_v2.character_life_experience_runtime import CharacterLifeExperienceRuntime
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.life_content_reading import read_character_life_experience_content
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER","1")
    path=tmp_path/"world.sqlite"
    _,ref=await _accepted_response(path,"我听着雨后的动静，心里松了一点。")
    ledger=SQLiteWorldLedger(path=path,world_id=WORLD)
    store=SQLiteImmutableLifeContentStore(path=path,world_id=WORLD)
    runtime=CharacterLifeExperienceRuntime(ledger=ledger,content_store=store,owner_actor_ref=ACTOR)
    identity=runtime.accept(world_id=WORLD,audit_cursor=_cursor(ledger),response_event_ref=ref)
    p=ledger.project()
    experience=next(e for e in p.experiences if e.experience_id==identity)
    def read(projection):
        return read_character_life_experience_content(store=store,projection=projection,
            experience=experience,actor_ref=ACTOR,viewer_privacy_ceiling="private")
    assert read(p)==read(p)
    with pytest.raises(ValueError,match="descriptor"):
        read(p.model_copy(update={"life_content_descriptors":()}))
    with pytest.raises(ValueError):
        read(p.model_copy(update={"world_occurrences":()}))
    store.close()
    ledger.close()
