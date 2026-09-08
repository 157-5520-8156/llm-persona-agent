"""Identity review material must be exactly covered by its existing source ref."""

import hashlib
import json

import pytest

from companion_daemon.world_v2.character_interior.inbound_wire import _source_closure_evidence
from companion_daemon.world_v2.companion_identity import (
    CompanionIdentityFrame,
    companion_identity_source_ref,
    companion_identity_source_refs,
)
from companion_daemon.world_v2.expression_draft import ExpressionDraft
from companion_daemon.world_v2.visible_source_closure_protocol import compact_source_reference_table
from test_character_interior_inbound_wire import _request


def _frame():
    return CompanionIdentityFrame(
        companion_name="知知", companion_aliases=("小知",), counterpart_name="测试者",
        stable_identity_facts=("名字是知知。",),
        shared_history_facts=("一起讨论过阅读计划。",),
        counterpart_history_facts=("用户小时候学过钢琴。",),
        personality_frame="慢热。", values=("尊重彼此的空间。",),
        speech_frame="自然聊天。", speech_examples=("测试语气样本。",),
        style_rules=("用日常语言。",), boundaries=("保留私人空间。",),
        base_prompt="不得由身份来源编号授权的完整提示。",
        appearance="外貌材料。", background="背景材料。",
        daily_life=("作息材料。",), first_message="预设开场材料。",
    )


def _evidence(identity, *, declared):
    refs = companion_identity_source_refs(identity)
    draft = ExpressionDraft(
        timing_choice="now",
        beats=({"modality": "text", "text": "我叫知知。"},),
        stance="answer_from_world",
        brief_rationale="Offline identity-source proof check.",
        world_claims=(
            ({"claim_text": "我叫知知", "scope": "current_world",
              "source_refs": tuple(refs.values())},)
            if declared else ()
        ),
    )
    return _source_closure_evidence(
        request=_request(), draft=draft,
        visible_context_json='{"actor_ref":"companion:test","slices":{}}',
        identity_frame=identity, include_visible_authorities=not declared,
    )


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


@pytest.mark.parametrize("declared", [True, False])
def test_actual_identity_evidence_is_exactly_bound_by_its_existing_ref(declared):
    evidence = _evidence(_frame(), declared=declared)
    entries = [entry for entry in evidence["entries"] if entry["kind"] == "identity_source"]
    assert {entry["scope"] for entry in entries} == {"stable_identity", "shared_history"}
    for entry in entries:
        assert _digest(entry["material"]) == entry["source_refs"][0].split("sha256:")[1]
    # Correctly bound bytes do not automatically qualify an identity source.
    rows = compact_source_reference_table(evidence)
    assert all(row["support_eligibility"] == "baseline_only" for row in rows)


@pytest.mark.parametrize("field,replacement", [
    ("counterpart_name", "另一位测试者"),
    ("shared_history_facts", ("不同的共同历史。",)),
    ("counterpart_history_facts", ("不同的用户历史。",)),
    ("base_prompt", "替换后的完整提示。"),
    ("appearance", "替换后的外貌。"),
    ("background", "替换后的背景。"),
    ("daily_life", ("替换后的作息。",)),
    ("speech_examples", ("替换后的示例。",)),
    ("first_message", "替换后的开场。"),
])
def test_unhashed_fields_cannot_change_material_under_the_same_stable_ref(field, replacement):
    original = _frame()
    changed = original.model_copy(update={field: replacement})
    assert companion_identity_source_ref(original) == companion_identity_source_ref(changed)

    def stable_material(frame):
        return next(entry["material"] for entry in _evidence(frame, declared=True)["entries"]
                    if entry.get("scope") == "stable_identity")

    assert stable_material(original) == stable_material(changed)


def test_original_identity_source_refs_remain_byte_compatible():
    frame = _frame()
    # Freeze existing source identities before the shared material producer is changed.
    assert companion_identity_source_refs(frame) == {
        "stable_identity": "identity-frame:sha256:b6226f25401b367752920cfc5a50ca78c5f0b4a678d038e9fb8a6c923a54adec",
        "shared_history": "identity-frame:shared-history:sha256:df16ecdb8d8987c84b1f0132ddbea3830d10d8cabd2fc820f84589ea8e8e5e86",
    }
    assert companion_identity_source_ref(frame, scope="counterpart_history") == "identity-frame:counterpart-history:sha256:a68a7a97a09f2bdae7d9fa8eee5ee1cd8fdd2feffd24f85a3c79f07443268626"
