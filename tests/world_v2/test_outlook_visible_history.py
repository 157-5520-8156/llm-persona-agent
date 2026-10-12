"""The published Context and Outlook reference permissions must agree."""

import json
from copy import deepcopy

import pytest
import pytest_asyncio

from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope, context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.lived_story import anchor_catalog
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_outlook_runtime import WorldOutlookRuntime
from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore
from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
from companion_daemon.world_v2.world_outlook_history import prepare_history_offer
from test_life_development_runtime import WORLD_ID, OWNER, _manifest, _projection_cursor
from test_world_consequence_aftermath import _settled_author_cohort
from test_world_outlook import Author, draft_value
from test_world_outlook_continuity import retime


def test_outlook_can_share_repeated_authority_hashes_without_changing_other_encoders():
    from companion_daemon.world_v2.shared_string_view import pack_shared_strings, unpack_shared_strings
    from companion_daemon.world_v2.world_outlook_request import encode_outlook_request
    packet = {"condition_sources": [], "authority_hashes": ["a" * 64] * 12}
    default = pack_shared_strings(packet)
    assert default["strings"] == {}
    compact = pack_shared_strings(packet, min_value_bytes=64)
    assert unpack_shared_strings(compact) == packet
    raw = encode_outlook_request(packet, byte_limit=400)
    assert len(raw.encode()) < 400
    assert decode_outlook_request(raw) == packet


def test_outlook_authority_hash_alias_is_exact_and_does_not_rewrite_prose():
    from companion_daemon.world_v2.shared_string_view import pack_shared_strings
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_response, RESPONSE_REFERENCE_CONTRACT
    digest = "a" * 64
    packet = pack_shared_strings({"response_reference_contract": RESPONSE_REFERENCE_CONTRACT,
                                  "hashes": [digest] * 12}, min_value_bytes=64)
    alias = next(key for key, value in packet["strings"].items() if value == digest)
    raw = json.dumps({"authority_hash": alias, "text": alias})
    assert decode_outlook_response(raw, request_raw=json.dumps(packet)) == {"authority_hash": digest, "text": alias}


@pytest_asyncio.fixture
async def visible_history(tmp_path):
    path = tmp_path / "world.sqlite"
    await _settled_author_cohort(path)
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    content = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    projection = ledger.project()
    wake = ledger.lookup_event_commit("event:clock:consequence:21m")[0]
    capsule = context_capsule_compiler_from_ledger(
        ledger=ledger, relevance_scope=ContextRelevanceScope(actor_ref=OWNER),
        life_content_store=content,
    ).compile(query_from_projection(projection, actor_ref=OWNER, trigger_ref=wake.event_id))
    context = json.loads(capsule.model_content_json)
    anchor = next(iter(anchor_catalog(projection, actor_ref=OWNER).values()))
    assert any(item["value"].get("occurrence_id") == anchor.source_ref
               and item["value"].get("content") for item in context["slices"]["world_life"]["items"])
    try:
        yield ledger, wake, context, anchor
    finally:
        content.close()
        ledger.close()


@pytest.mark.asyncio
async def test_visible_settlement_is_citable_without_a_second_retrieval(visible_history, tmp_path):
    ledger, wake, context, anchor = visible_history
    draft = retime(draft_value(wake), ledger.project(), wake)
    draft["threads"][0]["history_anchors"] = [anchor.model_dump(mode="json")]
    # One author call only: a bad reference cannot be hidden by a correction.
    author = Author([{"decision": "revise", "draft": draft}])
    runtime = WorldOutlookRuntime(
        ledger=ledger, actor_ref=OWNER, author=author,
        store=SQLiteWorldOutlookStore(path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref=OWNER),
    )
    before = ledger.project()
    try:
        result = await runtime.refresh(projection=before, context=context,
                                       manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
        assert result["status"] == "ready", result
        assert len(author.calls) == 1
        packet = decode_outlook_request(author.calls[0][1]["content"])
        assert packet["world_context"] == context
        assert packet["history_material"] == []
        assert [item["anchor"] for item in packet["history_offer"]["items"]] == [anchor.model_dump(mode="json")]
        assert ledger.project() == before
    finally:
        runtime.close()


def offer(ledger, context, *, projection=None, history=()):
    return prepare_history_offer(projection=projection or ledger.project(), actor_ref=OWNER,
                                 context=context, history_material=history, current_outlook={})


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "hidden", "other_world", "other_actor", "stale_cursor", "unavailable", "withheld",
    "source_hash", "value_hash", "source_ref", "source_revision", "content_hash", "quoted_only",
    "no_descriptor", "unsettled", "not_participant",
])
async def test_ineligible_or_undisplayed_sources_never_gain_an_offer(visible_history, case):
    ledger, _, original, anchor = visible_history
    context = deepcopy(original)
    state = ledger.project()
    lane = context["slices"]["world_life"]
    item = next(x for x in lane["items"] if x["item_ref"] == anchor.source_ref)
    lane["items"] = [item]
    if case == "hidden":
        lane["items"] = []  # The resolver proof/source_refs deliberately remain.
    elif case == "other_world":
        context["world_id"] = "world:other"
    elif case == "other_actor":
        context["actor_ref"] = "actor:other"
    elif case == "stale_cursor":
        context["ledger_sequence"] -= 1
    elif case == "unavailable":
        lane["availability"] = "unavailable"
    elif case == "withheld":
        item["privacy_class"] = "withhold"
    elif case in {"source_hash", "value_hash"}:
        item[case] = "0" * 64
    elif case == "source_ref":
        item["value"]["source"]["authority_event_ref"] = "event:forged"
    elif case == "source_revision":
        item["value"]["source"]["authority_world_revision"] += 1
    elif case == "content_hash":
        item["value"]["content"]["content_payload_hash"] = "0" * 64
    elif case == "quoted_only":
        context["slices"]["recent_dialogue"] = {"items": [item, {"text": json.dumps(item)}]}
        lane["items"] = []
    elif case == "no_descriptor":
        state = state.model_copy(update={"life_content_descriptors": ()})
    else:
        o = state.world_occurrences[0]
        update = {"status": "active"} if case == "unsettled" else {"participant_refs": ("actor:other",)}
        state = state.model_copy(update={"world_occurrences": (o.model_copy(update=update),)})
    assert offer(ledger, context, projection=state).anchors == ()


@pytest.mark.asyncio
async def test_offer_deduplicates_visible_and_retrieved_sources_without_copying_text(visible_history):
    ledger, _, context, anchor = visible_history
    history = [{"anchor": anchor.model_dump(mode="json"), "text": "同一段已检索材料"}]
    result = offer(ledger, context, history=history)
    assert result.anchors == (anchor,)
    assert "同一段已检索材料" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["visible", "retrieved", "prior"])
@pytest.mark.parametrize("privacy", ["personal", "withhold"])
async def test_displayed_content_keeps_its_stronger_privacy_floor(visible_history, route, privacy):
    from companion_daemon.world_v2.world_outlook import WorldOutlookDraft
    from companion_daemon.world_v2.context_capsule import canonical_value_hash
    from companion_daemon.world_v2.world_life_context import WorldLifeContextItem
    ledger, wake, context, anchor = visible_history
    state = ledger.project()
    occurrence = state.world_occurrences[0]
    # The environment can be public while the actual selected content is personal.
    state = state.model_copy(update={"world_occurrences": (occurrence.model_copy(update={"visibility": "public"}),)})
    state = state.model_copy(update={"life_content_descriptors": tuple(
        descriptor.model_copy(update={"privacy_class": privacy}) for descriptor in state.life_content_descriptors
    )})
    context = deepcopy(context)
    for item in context["slices"]["world_life"]["items"]:
        if item["item_ref"] == anchor.source_ref:
            item["privacy_class"] = item["value"]["privacy_class"] = privacy
            item["value"]["content"]["privacy_class"] = privacy
            item["value_hash"] = canonical_value_hash(WorldLifeContextItem.model_validate_json(json.dumps(item["value"])))
    result = prepare_history_offer(
        projection=state, actor_ref=OWNER, context=context if route == "visible" else {},
        history_material=[{"anchor": anchor.model_dump(mode="json"), "text": "已检索的来源"}] if route == "retrieved" else [],
        current_outlook={"threads": [{"history_anchors": [anchor.model_dump(mode="json")]}]} if route == "prior" else {},
    )
    if privacy == "withhold":
        assert result.anchors == ()
    else:
        assert result.anchors == (anchor,)
        assert result.items[0].privacy_class == "personal"
    draft = retime(draft_value(wake), state, wake)
    draft["threads"][0]["history_anchors"] = [anchor.model_dump(mode="json")]
    draft["opportunities"][0]["execution"]["privacy_class"] = "public"
    with pytest.raises(ValueError, match="privacy|not offered"):
        result.validate_privacy(WorldOutlookDraft.model_validate_json(json.dumps(draft)))


@pytest.mark.asyncio
async def test_captured_offer_survives_restart_without_new_context_permissions(visible_history, tmp_path, monkeypatch):
    class Crash(BaseException):
        pass

    ledger, wake, context, anchor = visible_history
    draft = retime(draft_value(wake), ledger.project(), wake)
    draft["threads"][0]["history_anchors"] = [anchor.model_dump(mode="json")]
    author = Author([{"decision": "revise", "draft": draft}])
    path = tmp_path / "outlook.sqlite"
    store = SQLiteWorldOutlookStore(path=path, world_id=WORLD_ID, actor_ref=OWNER)
    runtime = WorldOutlookRuntime(ledger=ledger, actor_ref=OWNER, author=author, store=store)

    def crash(*args, **kwargs):
        raise Crash()

    monkeypatch.setattr(store, "install", crash)
    arguments = dict(projection=ledger.project(), manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
    try:
        with pytest.raises(Crash):
            await runtime.refresh(context=context, **arguments)
    finally:
        runtime.close()
    reopened = WorldOutlookRuntime(
        ledger=ledger, actor_ref=OWNER, author=author,
        store=SQLiteWorldOutlookStore(path=path, world_id=WORLD_ID, actor_ref=OWNER),
    )
    try:
        assert (await reopened.refresh(context={}, **arguments))["status"] == "ready"
        assert len(author.calls) == 1
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_legacy_captured_request_does_not_gain_current_context_permissions(visible_history, tmp_path, monkeypatch):
    import companion_daemon.world_v2.world_outlook_runtime as module

    class Crash(BaseException):
        pass

    ledger, wake, context, anchor = visible_history
    draft = retime(draft_value(wake), ledger.project(), wake)
    draft["threads"][0]["history_anchors"] = [anchor.model_dump(mode="json")]
    author = Author([{"decision": "revise", "draft": draft}] * 2)
    path = tmp_path / "legacy-outlook.sqlite"
    runtime = WorldOutlookRuntime(
        ledger=ledger, actor_ref=OWNER, author=author,
        store=SQLiteWorldOutlookStore(path=path, world_id=WORLD_ID, actor_ref=OWNER),
    )
    original_encode = module.encode_outlook_request

    def legacy_encode(packet, **kwargs):
        return original_encode({k: v for k, v in packet.items() if k != "history_offer"}, **kwargs)

    monkeypatch.setattr(module, "encode_outlook_request", legacy_encode)
    record = runtime._record_attempt

    def crash_after_capture(*args, **kwargs):
        record(*args, **kwargs)
        raise Crash()

    monkeypatch.setattr(runtime, "_record_attempt", crash_after_capture)
    arguments = dict(projection=ledger.project(), context=context,
                     manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
    try:
        with pytest.raises(Crash):
            await runtime.refresh(**arguments)
    finally:
        runtime.close()
    monkeypatch.setattr(module, "encode_outlook_request", original_encode)
    reopened = WorldOutlookRuntime(
        ledger=ledger, actor_ref=OWNER, author=author,
        store=SQLiteWorldOutlookStore(path=path, world_id=WORLD_ID, actor_ref=OWNER),
    )
    try:
        assert (await reopened.refresh(**arguments))["status"] == "technical_failure"
        assert len(author.calls) == 2
        assert all("history_offer" not in decode_outlook_request(call[1]["content"]) for call in author.calls)
        assert reopened.store.head() is None
    finally:
        reopened.close()
