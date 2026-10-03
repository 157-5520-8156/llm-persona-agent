from datetime import date

import pytest
import yaml

from companion_daemon.safe_yaml import load_yaml_text


def test_safe_yaml_matches_previous_parser_and_isolates_mutation():
    text = """date: 2004-04-15
base: &base {yes_value: true, empty: null, values: [中文, 2]}
copy: {<<: *base, no_value: false}
"""
    expected = yaml.safe_load(text)
    actual = load_yaml_text(text)
    assert actual == expected
    assert actual["date"] == date(2004, 4, 15)
    actual["base"]["values"].append("caller mutation")
    assert load_yaml_text(text) == expected


def test_safe_yaml_content_key_does_not_reuse_old_file_contents(tmp_path):
    path = tmp_path / "world.yaml"
    path.write_text("value: first", encoding="utf-8")
    assert load_yaml_text(path.read_text()) == {"value": "first"}
    path.write_text("value: changed", encoding="utf-8")
    assert load_yaml_text(path.read_text()) == {"value": "changed"}


@pytest.mark.parametrize("text", ["!!python/object/apply:os.system ['true']", "values: ["])
def test_safe_yaml_keeps_unsafe_tags_and_malformed_yaml_rejected(text):
    with pytest.raises(yaml.YAMLError):
        load_yaml_text(text)
