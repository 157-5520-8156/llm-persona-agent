from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.json_wire_repair import (
    close_unclosed_json_containers,
    escape_unescaped_quotes_in_json_strings,
    loads_one_json_object,
    strip_json_fence,
)


def test_strip_json_fence_removes_closed_markdown() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert strip_json_fence(raw) == '{"a": 1}'


def test_strip_json_fence_rejects_unclosed_markdown() -> None:
    with pytest.raises(ValueError, match="JSON fence is unclosed"):
        strip_json_fence('```json\n{"a": 1}')


def test_escape_unescaped_quotes_in_dialogue() -> None:
    raw = '{"stuck_with_me":"他说"我是认真问的"，这句话让我有点不知道怎么接。"}'
    repaired = escape_unescaped_quotes_in_json_strings(raw)
    assert json.loads(repaired)["stuck_with_me"] == (
        '他说"我是认真问的"，这句话让我有点不知道怎么接。'
    )


def test_close_unclosed_json_containers_appends_missing_wrapper() -> None:
    text = '{"result":{"status":"ok"}'
    assert close_unclosed_json_containers(text) == '{"result":{"status":"ok"}}'


def test_close_unclosed_json_containers_leaves_truncated_string_alone() -> None:
    text = '{"result":{"summary":"half'
    assert close_unclosed_json_containers(text) == text


def test_close_unclosed_json_containers_leaves_negative_depth_alone() -> None:
    text = '{"a":1}}'
    assert close_unclosed_json_containers(text) == text


def test_loads_one_json_object_repairs_fenced_object_missing_wrapper_brace() -> None:
    body = json.dumps({"result": {"ok": True}}, ensure_ascii=False)
    raw = "```json\n" + body[:-1] + "\n```"
    decoded = loads_one_json_object(raw)
    assert decoded == {"result": {"ok": True}}


def test_loads_one_json_object_takes_first_object_when_extra_data_follows() -> None:
    raw = json.dumps({"result": {"ok": True}}) + json.dumps({"note": "trailing"})
    decoded = loads_one_json_object(raw)
    assert decoded == {"result": {"ok": True}}


def test_loads_one_json_object_rejects_truncated_string() -> None:
    with pytest.raises(ValueError, match="did not return one JSON object"):
        loads_one_json_object('{"result":{"summary":"half')


def test_loads_one_json_object_rejects_array_root() -> None:
    with pytest.raises(ValueError, match="got type=list"):
        loads_one_json_object("[1, 2]")
