import json

import httpx

from companion_daemon.llm import model_provider_request_identity_scope, provider_invocation_request_hash
from companion_daemon.world_v2.longitudinal_model_input_capture import _request_material
from companion_daemon.world_v2.reference_wire import expand_reference_view, prepare_reference_view


def test_private_capture_can_rebuild_failed_compact_requests_without_sending_bindings():
    original = {"source_refs": ["event:" + str(index) + ":" + "a" * 64 for index in range(20)]}
    view, bindings = prepare_reference_view(original)
    messages = [{"role": "user", "content": json.dumps(view)}]
    extras = {"reference_bindings": bindings}
    digest = provider_invocation_request_hash(messages=messages, temperature=0.8, identity_extras=extras)
    request = httpx.Request(
        "POST", "https://fixture.invalid/chat/completions",
        json={"model": "fixture", "messages": messages, "temperature": 0.8},
        headers={"Authorization": "Bearer never-archive-this-credential"},
    )
    with model_provider_request_identity_scope(request_hash=digest, identity_extras=extras):
        captured = _request_material(request)
    local = captured["local_reference_identity"]
    assert local["model_facing"] is False
    sent = json.loads(json.loads(captured["model_content_json"])["messages"][0]["content"])
    assert expand_reference_view(sent, local["identity_extras"]["reference_bindings"]) == original
    assert "entries" not in sent["reference_dictionary"]
    assert "never-archive-this-credential" not in json.dumps(captured)
    assert _request_material(request).get("local_reference_identity") is None


def test_capture_never_exports_unrecognized_private_identity_fields():
    extras = {"reference_bindings": {}, "credential": "not-for-capture"}
    request = httpx.Request("POST", "https://fixture.invalid", json={"messages": []})
    with model_provider_request_identity_scope(request_hash="a" * 64, identity_extras=extras):
        captured = _request_material(request)
    assert "local_reference_identity" not in captured
    assert "not-for-capture" not in json.dumps(captured)
