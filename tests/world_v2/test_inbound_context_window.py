from copy import deepcopy
import json

from companion_daemon.world_v2.character_interior.inbound_context_window import bounded_inbound_context
from companion_daemon.world_v2.character_interior.inbound_tool_contract import expand_atomic_slim_payload
from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
from companion_daemon.world_v2.shared_string_view import pack_shared_strings


def test_window_retains_current_state_facts_counterpart_and_selected_recall():
    dialogue = [{'speaker': speaker, 'text': f'{speaker}:{i}', 'dialogue_id': str(i)}
                for i in range(8) for speaker in ('counterpart','companion')]
    materials = {
        'recent_dialogue': {'stable_turns': dialogue, 'volatile_last_turn': {'speaker':'counterpart','text':'now'}},
        'appraisals': {'columns':['ref','since','readings'], 'stable_rows': [
            [str(i),f'2026-09-{i+1:02d}T10:00:00Z',['own interpretation']] for i in range(8)]},
        'affect': {'stable_entries': [{'episode_id':'old-active','status':'active','components':[
            {'component_id':'anger:1','dimension':'anger','intensity_bp':5000,'appraisal_refs':['proof']}]}]},
        'relationship': [{'stage':'friend'}], 'unresolved':[{'text':'an old unfinished promise'}],
        'relevant_facts':[{'text':'Friday at three','subject_ref':'counterpart'}],
        'selected_recall':{'items':[{'text':'an older memory'}]},
        'private_impressions':[{'reflection_summary':'an uncertain belief'}],
        'moments_i_can_share':{'candidates':['photo:1']},
    }
    original={'inner_life_snapshot': {'materials':materials}, 'expression_hard_boundaries':{'sources':['a','b']}}
    before=deepcopy(original)
    after=bounded_inbound_context(original)
    assert original==before
    current=after['inner_life_snapshot']['materials']
    for key in ('relationship','unresolved','relevant_facts','selected_recall','private_impressions','moments_i_can_share'):
        assert current[key]==materials[key]
    assert after['expression_hard_boundaries']==original['expression_hard_boundaries']
    assert [r for r in current['recent_dialogue']['stable_turns']if r['speaker']=='counterpart']==[r for r in dialogue if r['speaker']=='counterpart']
    assert len([r for r in current['recent_dialogue']['stable_turns']if r['speaker']=='companion'])==2
    assert current['appraisals']['stable_rows']==materials['appraisals']['stable_rows'][-4:]
    assert current['affect']['stable_entries'][0]['components'][0]=={
        'component_id':'anger:1','dimension':'anger','intensity_bp':5000}
    assert after['context_window']['omitted']=={'older_companion_utterances':6,'older_appraisal_narratives':4}
    packed=deepcopy(original)
    packed['inner_life_snapshot']['materials']=pack_shared_strings(materials)
    assert bounded_inbound_context(packed)==after


def test_slim_recall_canonicalizes_set_order_without_changing_members_or_other_fields():
    value={'private_turn_state':{'inner_state_summary':'想找找','attended_source_refs':[]},
        'recall_request':{'query_text':'往事','memory_kinds':['semantic','episodic','semantic'],
                          'link_refs':['z','a','z'],'include_historical':True,'limit':3}}
    raw=json.dumps(value)
    before=deepcopy(value)
    expanded=expand_atomic_slim_payload({'result_kind':'recall','payload_json':raw},recall_allowed=True)
    parsed=CharacterRecallRequest.model_validate_json(json.dumps(expanded['recall_request']))
    assert parsed.memory_kinds==('episodic','semantic') and parsed.link_refs==('a','z')
    assert parsed.query_text=='往事' and parsed.include_historical and parsed.limit==3
    assert value==before and expanded['private_turn_state']==before['private_turn_state']


def test_slim_normalization_never_drops_an_unknown_filter_member():
    import pytest
    value={'private_turn_state':{'inner_state_summary':'想找找'},
           'recall_request':{'query_text':'q','memory_kinds':['semantic','unknown_kind']}}
    expanded=expand_atomic_slim_payload({'result_kind':'recall','payload_json':json.dumps(value)},recall_allowed=True)
    with pytest.raises(ValueError):
        CharacterRecallRequest.model_validate_json(json.dumps(expanded['recall_request']))


def test_bounded_correction_keeps_the_initial_prefix_and_original_feedback():
    packet={'inner_life_snapshot':{'materials':{}},'current_trigger_message':{'text':'now'}}
    first=bounded_inbound_context(packet)
    feedback={'failure_detail':'exact previous bytes', 'reference':'@r:0'}
    second=bounded_inbound_context({**packet,'role_result_correction':feedback})
    assert list(second)[-1]=='role_result_correction'
    assert second.pop('role_result_correction')==feedback
    assert first==second


def test_compact_appraisals_orders_by_accepted_at_not_sha256_source_ref():
    from companion_daemon.world_v2.character_interior.appraisal_model_view import (
        compact_appraisals_for_model_view,
    )
    from companion_daemon.world_v2.present_prompt import cache_stable_appraisals

    # Simulate snapshot_compiler sorting living_appraisals by SHA-256 source_ref:
    # 028d is oldest (yesterday 1:34 AM), 3e0a is newest (tonight 20:12),
    # and ff0c is an earlier turn (tonight 20:03) with a larger hex prefix.
    raw_appraisals = [
        {
            "source_ref": "appraisal:compiled:028d606e5",
            "confidence_bp": 8000,
            "accepted_at": "2026-10-28T17:34:42+00:00",
            "expires_at": "2026-10-30T17:34:42+00:00",
            "hypotheses": [{"meaning": "快一点半了，说好要睡的人还赖着"}],
        },
        {
            "source_ref": "appraisal:compiled:3e0a129d1",
            "confidence_bp": 8500,
            "accepted_at": "2026-10-29T12:12:25+00:00",
            "expires_at": "2026-10-31T12:12:25+00:00",
            "hypotheses": [{"meaning": "他承认今天早十的课翘了去睡大觉"}],
        },
        {
            "source_ref": "appraisal:compiled:ff0c15f26",
            "confidence_bp": 8000,
            "accepted_at": "2026-10-29T12:03:37+00:00",
            "expires_at": "2026-10-31T12:03:37+00:00",
            "hypotheses": [{"meaning": "他甩个表情包接梗，不是真交代昨晚几点睡"}],
        },
    ]
    compacted = compact_appraisals_for_model_view(raw_appraisals, logical_time=None)
    split = cache_stable_appraisals(compacted)
    assert [row[0] for row in split["stable_rows"]] == [
        "appraisal:compiled:028d606e5",
        "appraisal:compiled:ff0c15f26",
    ]
    assert split["volatile_last_row"][0] == "appraisal:compiled:3e0a129d1"


def test_bounded_inbound_context_omits_recent_authored_signals_dogma_from_relationship():
    packet = {
        "inner_life_snapshot": {
            "materials": {
                "relationship": [
                    {
                        "stage": "close_friend",
                        "recent_authored_signals": [
                            {"signal_code": "最新一条心意读法", "rationale_code": "r1", "confidence_bp": 8000},
                            {"signal_code": "上一轮重复的拿乔读法", "rationale_code": "r2", "confidence_bp": 8000},
                        ],
                    }
                ]
            }
        }
    }
    after = bounded_inbound_context(packet)
    assert "recent_authored_signals" not in after["inner_life_snapshot"]["materials"]["relationship"][0]


def test_bounded_inbound_context_builds_citable_memory_shortcuts():
    packet = {
        "inner_life_snapshot": {
            "materials": {
                "automatic_prefetch": {
                    "items": [
                        {
                            "source_ref": "prehistory-story:jiaxing-dumplings",
                            "text": "小时候在嘉兴过年跟妈妈一起包饺子，把硬币包进了破皮的那只里",
                        }
                    ]
                },
                "relevant_facts": [
                    {
                        "source_ref": "fact:geoff-major",
                        "source_excerpt": "我在成都理工读软件工程，开学大四",
                    }
                ],
            }
        },
        "expression_hard_boundaries": {
            "source_ref_aliases": {
                "S12": "prehistory-story:jiaxing-dumplings",
                "S04": "fact:geoff-major",
            },
            "world_claim_source_refs": {
                "past_world": ["S12"],
                "shared_history": ["S04"],
            },
        },
    }
    after = bounded_inbound_context(packet)
    assert after["context_window"]["citable_memory_shortcuts"] == [
        {
            "alias": "S12",
            "scopes": ["past_world"],
            "slice": "automatic_prefetch",
            "preview": "小时候在嘉兴过年跟妈妈一起包饺子，把硬币包进了破皮的那只里",
        },
        {
            "alias": "S04",
            "scopes": ["shared_history"],
            "slice": "relevant_facts",
            "preview": "我在成都理工读软件工程，开学大四",
        },
    ]


def test_citable_memory_shortcuts_indexes_perception_and_biographical_coordinates():
    from companion_daemon.world_v2.present_prompt import compile_slim_consider_payload

    packet = {
        "inner_life_snapshot": {
            "materials": {
                "perception": [
                    {
                        "influence_id": "life-influence:external-perception:dance-clip",
                        "title": "【阿初】祈月舞",
                        "summary": "收束动作很稳，手腕转得干净",
                    }
                ],
            }
        },
        "expression_hard_boundaries": {
            "source_ref_aliases": {
                "S05": "life-influence:external-perception:dance-clip",
                "S09": "biography-coordinate:sha256:essay-closed",
            },
            "world_claim_source_refs": {
                "current_world": ["S05", "S09"],
                "past_world": ["S05"],
            },
            "biographical_coordinate_authority": [
                {
                    "source_ref": "S09",
                    "scope": "current_world",
                    "field_path": "/settled_biographical_coordinates/biography:essay_team_invite_open",
                    "value": {
                        "summary": "合写组队邀请已关闭：范予安在今晚七点半等待无果后以个人名义单独提交了征文报名",
                    },
                }
            ],
        },
    }
    after = bounded_inbound_context(packet)
    assert after["context_window"]["citable_memory_shortcuts"] == [
        {
            "alias": "S05",
            "scopes": ["current_world", "past_world"],
            "slice": "perception",
            "preview": "【阿初】祈月舞 — 收束动作很稳，手腕转得干净",
        },
        {
            "alias": "S09",
            "scopes": ["current_world"],
            "slice": "biographical_context",
            "preview": "合写组队邀请已关闭：范予安在今晚七点半等待无果后以个人名义单独提交了征文报名",
        },
    ]

    compiled = compile_slim_consider_payload(
        {
            "meaning_of_this": "他问我征文合写那事最后定了没",
            "my_state": "被问到有点别扭，如实告诉他黄了",
            "messages": ["征文那事啊 黄了"],
            "world_claims": [
                {
                    "claim_text": "合写组队邀请已关闭，范予安以个人名义单独提交了报名",
                    "scope": "current_world",
                    "source_ref": "S09",
                }
            ],
        }
    )
    assert compiled is not None
    assert compiled["expression_draft"]["world_claims"] == [
        {
            "claim_text": "合写组队邀请已关闭，范予安以个人名义单独提交了报名",
            "scope": "current_world",
            "source_refs": ["S09"],
        }
    ]


def test_citable_memory_shortcuts_marks_explicitly_unshared_private_topics():
    packet = {
        "inner_life_snapshot": {
            "materials": {
                "biographical_context": [
                    {
                        "settled_biographical_coordinates": [
                            {
                                "coordinate_ref": "biography:essay_team_invite_open",
                                "summary": "合写组队邀请已关闭：范予安在今晚七点半等待无果后以个人名义单独提交了征文报名",
                                "shared_in_chat": False,
                            },
                            {
                                "coordinate_ref": "biography:eval_queue_remedy_channel_open",
                                "summary": "柜台核验部分受理：综测材料有一页复印件页码标注与目录不符",
                            },
                        ]
                    }
                ],
            }
        },
        "expression_hard_boundaries": {
            "source_ref_aliases": {
                "S09": "biography-coordinate:sha256:essay-closed",
                "S10": "biography-coordinate:sha256:eval-remedy",
            },
            "world_claim_source_refs": {
                "current_world": ["S09", "S10"],
            },
            "biographical_coordinate_authority": [
                {
                    "source_ref": "S09",
                    "scope": "current_world",
                    "field_path": "/settled_biographical_coordinates/biography:essay_team_invite_open",
                    "value": {
                        "summary": "合写组队邀请已关闭：范予安在今晚七点半等待无果后以个人名义单独提交了征文报名",
                        "shared_in_chat": False,
                    },
                },
                {
                    "source_ref": "S10",
                    "scope": "current_world",
                    "field_path": "/settled_biographical_coordinates/biography:eval_queue_remedy_channel_open",
                    "value": {
                        "summary": "柜台核验部分受理：综测材料有一页复印件页码标注与目录不符",
                    },
                },
            ],
        },
    }
    after = bounded_inbound_context(packet)
    shortcuts = after["context_window"]["citable_memory_shortcuts"]
    assert shortcuts[0]["alias"] == "S09" and shortcuts[0].get("shared_in_chat") is False
    assert shortcuts[1]["alias"] == "S10" and "shared_in_chat" not in shortcuts[1]


def test_bounded_inbound_context_restores_dropped_companion_utterance_text_in_conversation_entries():
    from companion_daemon.world_v2.model_context_reading import present_context_evidence

    packet = {
        "inner_life_snapshot": {
            "contract": "inner-life-snapshot.1",
            "cursor": {"logical_time": "2026-10-31T01:05:00Z"},
            "materials": {
                "conversation": [
                    "我（10 分钟前）：今天周六 旧书市场开市 我有点想去逛逛",
                    "他（4 分钟前）：去看看咯，难得有这种东西看",
                    "我（1 分钟前）：文学摊那车特价书还摆着",
                    "我（2 分钟前）：你要去就自己找过来",
                ],
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "speaker": "companion",
                            "text": "今天周六 旧书市场开市 我有点想去逛逛",
                            "dialogue_id": "utterance-1",
                            "occurred_at": "2026-10-31T00:55:00Z",
                        },
                        {
                            "speaker": "counterpart",
                            "text": "去看看咯，难得有这种东西看",
                            "dialogue_id": "utterance-2",
                            "occurred_at": "2026-10-31T01:01:00Z",
                        },
                        {
                            "speaker": "companion",
                            "text": "文学摊那车特价书还摆着",
                            "dialogue_id": "utterance-3",
                            "occurred_at": "2026-10-31T01:04:00Z",
                        },
                        {
                            "speaker": "companion",
                            "text": "你要去就自己找过来",
                            "dialogue_id": "utterance-4",
                            "occurred_at": "2026-10-31T01:03:00Z",
                        },
                    ],
                },
            },
        }
    }
    first_pass = present_context_evidence(packet)
    assert first_pass["inner_life_snapshot"]["materials"]["conversation"]["entries"][0] == {
        "source_ref": "utterance-1",
        "display_prefix": "我（10 分钟前）：",
    }
    bounded = bounded_inbound_context(first_pass)
    entries = bounded["inner_life_snapshot"]["materials"]["conversation"]["entries"]
    assert entries[0] == {"line": "我（10 分钟前）：今天周六 旧书市场开市 我有点想去逛逛"}
    assert entries[1] == {
        "source_ref": "utterance-2",
        "line": "他（4 分钟前）：去看看咯，难得有这种东西看",
    }
    assert entries[2] == {
        "source_ref": "utterance-3",
        "line": "我（1 分钟前）：文学摊那车特价书还摆着",
    }
    assert entries[3] == {
        "source_ref": "utterance-4",
        "line": "我（2 分钟前）：你要去就自己找过来",
    }


def test_bounded_inbound_context_omits_superseded_chat_appraisals_while_keeping_life_appraisals():
    packet = {
        "inner_life_snapshot": {
            "materials": {
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "speaker": "counterpart",
                            "text": "太御了我吱吱",
                            "dialogue_id": "d1",
                            "occurred_at": "2026-10-31T05:14:13Z",
                            "continuity_reasons": ["recent"],
                        },
                        {
                            "speaker": "counterpart",
                            "text": "你拍张照给我不就是在等我夸嘛",
                            "dialogue_id": "d2",
                            "occurred_at": "2026-10-31T05:14:43Z",
                            "continuity_reasons": ["recent"],
                        },
                    ],
                    "volatile_last_turn": {
                        "speaker": "counterpart",
                        "text": "书和你摆在一起当然是你更吸引我啦",
                        "dialogue_id": "d3",
                        "occurred_at": "2026-10-31T05:18:44Z",
                        "continuity_reasons": ["current_turn"],
                    },
                },
                "appraisals": {
                    "columns": ["ref", "conf", "since", "until", "readings", "excerpts"],
                    "stable_rows": [
                        [
                            "appraisal:npc-zhou",
                            6800,
                            "2026-10-31T02:15:04Z",
                            "2026-11-02T04:00:00Z",
                            [["周雨微这是把我划进待确认名单来施压", "npc", "partly_controllable", "moderate"]],
                            [],
                        ],
                        [
                            "appraisal:stale-wait",
                            7200,
                            "2026-10-31T05:04:02Z",
                            "2026-10-31T15:00:00Z",
                            [["他大概在吃饭或午休，没刷到我的结算报告", "user", "partly_controllable", "low"]],
                            [],
                        ],
                        [
                            "appraisal:stale-d1",
                            5000,
                            "2026-10-31T05:14:13Z",
                            "2026-11-02T05:14:13Z",
                            [["他看见我那张自拍，冒出来一句太御了我吱吱", "unknown", "partly_controllable", "moderate"]],
                            ["太御了我吱吱"],
                        ],
                    ],
                    "volatile_last_row": [
                        "appraisal:latest-d2",
                        5000,
                        "2026-10-31T05:14:43Z",
                        "2026-11-02T05:14:43Z",
                        [["他这句是接着我那句御什么御来的", "unknown", "partly_controllable", "moderate"]],
                        ["你拍张照给我不就是在等我夸嘛"],
                    ],
                },
            }
        }
    }
    after = bounded_inbound_context(packet)
    appraisals = after["inner_life_snapshot"]["materials"]["appraisals"]
    assert appraisals["stable_rows"] == []
    assert appraisals["volatile_last_row"][0] == "appraisal:npc-zhou"


def test_bounded_inbound_context_omits_superseded_chat_private_impressions():
    packet = {
        "inner_life_snapshot": {
            "materials": {
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "speaker": "companion",
                            "text": "昨晚暖气管嗡嗡响",
                            "dialogue_id": "d0",
                            "occurred_at": "2026-10-30T20:44:50Z",
                        },
                        {
                            "speaker": "counterpart",
                            "text": "四点还不睡？没感冒吧",
                            "dialogue_id": "d1",
                            "occurred_at": "2026-10-31T00:46:29Z",
                            "continuity_reasons": ["recent"],
                        },
                        {
                            "speaker": "counterpart",
                            "text": "我现在刚醒",
                            "dialogue_id": "d2",
                            "occurred_at": "2026-10-31T01:55:00Z",
                            "continuity_reasons": ["current_turn"],
                        },
                    ]
                },
                "private_impressions": [
                    {
                        "source_ref": "impression:stale-morning",
                        "subject_ref": "user:geoff",
                        "expiry_condition": "until_appraisal_contradicted",
                        "first_seen": "2026-10-30T23:07:04Z",
                        "last_supported": "2026-10-30T23:07:04Z",
                        "reflection_summary": "他还没回，等他上线看第一句怎么接",
                    },
                    {
                        "source_ref": "impression:linwan-arc",
                        "subject_ref": "agent:companion",
                        "expiry_condition": "until_appraisal_contradicted",
                        "first_seen": "2026-10-28T04:35:00+08:00",
                        "last_supported": "2026-10-30T16:00:02Z",
                        "reflection_summary": "林晚的事周五说清楚就好",
                    },
                ],
            }
        }
    }
    after = bounded_inbound_context(packet)
    impressions = after["inner_life_snapshot"]["materials"]["private_impressions"]
    assert [item["source_ref"] for item in impressions] == ["impression:linwan-arc"]
    assert after["context_window"]["omitted"]["superseded_private_impressions"] == 1


def test_pinned_turn_resolves_quoted_reply_texts_from_projection():
    from types import SimpleNamespace
    from companion_daemon.world_v2.pinned_turn import PinnedTurnCompiler

    projection = SimpleNamespace(
        stored_message_payloads=(
            SimpleNamespace(payload_ref="payload:beat-1", text="今天周六 旧书市场开市 我有点想去逛逛"),
            SimpleNamespace(payload_ref="payload:obs-2", text="去看看咯，难得有这种东西看"),
        ),
        expression_plan_manifests=(
            SimpleNamespace(
                beats=(
                    SimpleNamespace(
                        action=SimpleNamespace(action_id="action:send-1"),
                        text=None,
                        payload_ref="payload:beat-1",
                    ),
                )
            ),
        ),
        minimal_reply_manifests=(),
        execution_receipts=(
            SimpleNamespace(
                action_id="action:send-1",
                provider_ref="platform:message_id:1481255238",
            ),
        ),
        message_observations=(
            SimpleNamespace(
                source_event_id="qq:1001:99887766",
                payload_ref="payload:obs-2",
            ),
        ),
    )
    resolved = PinnedTurnCompiler._resolve_quoted_reply_texts(
        ("qq-message:1481255238", "qq-message:99887766"),
        projection,
    )
    assert resolved == (
        "qq-message:1481255238 -> 我：今天周六 旧书市场开市 我有点想去逛逛",
        "qq-message:99887766 -> 对方：去看看咯，难得有这种东西看",
    )


def test_private_impression_opportunity_picks_newest_appraisal_by_commit_revision():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from companion_daemon.world_v2.private_impression_producer import private_impression_opportunity

    older_appraisal = SimpleNamespace(
        appraisal_id="appraisal:compiled:ffffffff",
        status="active",
        subject_ref="agent:companion",
        hypotheses=(),
        origin=SimpleNamespace(
            accepted_event_ref="event:appraisal:old",
            change_id="change:old",
        ),
    )
    newer_appraisal = SimpleNamespace(
        appraisal_id="appraisal:compiled:00000001",
        status="active",
        subject_ref="agent:companion",
        hypotheses=(),
        origin=SimpleNamespace(
            accepted_event_ref="event:appraisal:new",
            change_id="change:new",
        ),
    )
    projection = SimpleNamespace(
        world_id="world:test",
        logical_time=datetime(2026, 10, 31, 2, 0, tzinfo=UTC),
        trigger_processes=(),
        private_impressions=(),
        private_impression_proposals=(),
        proposal_audits=(),
        committed_world_event_refs=(
            SimpleNamespace(
                event_id="event:appraisal:old",
                event_type="AppraisalAccepted",
                world_revision=10,
                continuation_refs=(),
            ),
            SimpleNamespace(
                event_id="event:appraisal:new",
                event_type="AppraisalAccepted",
                world_revision=20,
                continuation_refs=(),
            ),
            SimpleNamespace(
                event_id="event:obs:latest",
                event_type="ObservationRecorded",
                world_revision=21,
                continuation_refs=(),
            ),
        ),
        # Sorted in ascending appraisal_id order, so older_appraisal ("ffff") is at the end!
        appraisals=(newer_appraisal, older_appraisal),
    )
    opp = private_impression_opportunity(projection)
    assert opp is not None
    assert opp[1] == "event:appraisal:new"


def test_bounded_inbound_context_retains_durable_counter_evidence_impressions_embodied_moment_and_landmarks():
    from companion_daemon.world_v2.character_interior.inbound_context_window import (
        present_dialogue_observation_timing,
    )

    packet = {
        "inner_life_snapshot": {
            "materials": {
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "speaker": "counterpart",
                            "text": "明早早十我肯定起得来",
                            "dialogue_id": "d1",
                            "occurred_at": "2026-10-31T01:00:00Z",
                            "continuity_reasons": ["recent"],
                        },
                        {
                            "speaker": "companion",
                            "text": "行 我记着了",
                            "dialogue_id": "d2",
                            "delivery_state": "delivered",
                            "occurred_at": "2026-10-31T01:05:00Z",
                        },
                        {
                            "speaker": "counterpart",
                            "text": "刚才睡过头了哈哈",
                            "dialogue_id": "d3",
                            "occurred_at": "2026-10-31T01:15:00Z",
                            "continuity_reasons": ["recent"],
                        },
                        {
                            "speaker": "companion",
                            "text": "我就知道",
                            "dialogue_id": "d4",
                            "delivery_state": "delivered",
                            "occurred_at": "2026-10-31T01:16:00Z",
                        },
                    ],
                    "volatile_last_turn": {
                        "speaker": "counterpart",
                        "text": "别笑我了，你答应寄的书寄了没",
                        "dialogue_id": "d5",
                        "occurred_at": "2026-10-31T01:20:00Z",
                        "continuity_reasons": ["current_turn"],
                    },
                },
                "private_impressions": [
                    {
                        "source_ref": "impression:transient-wait",
                        "subject_ref": "user:geoff",
                        "expiry_condition": "until_appraisal_contradicted",
                        "first_seen": "2026-10-31T01:02:00Z",
                        "last_supported": "2026-10-31T01:02:00Z",
                        "reflection_summary": "他还没回，等他上线",
                    },
                    {
                        "source_ref": "impression:durable-idiom-and-promise",
                        "subject_ref": "user:geoff",
                        "expiry_condition": "until_counter_evidence",
                        "first_seen": "2026-10-31T01:05:00Z",
                        "last_supported": "2026-10-31T01:05:00Z",
                        "reflection_summary": "他信誓旦旦立了早十flag；另外答应给他挑一本旧书寄过去",
                    },
                ],
                "lived_moment": {
                    "contract": "lived-material-links.1",
                    "current_moment": "此刻正在：去食堂吃饭；当下环境：女生宿舍402 · 窗外下着雨；当前状态负荷：体力偏低；已连续清醒 16 小时",
                },
                "automatic_prefetch": {
                    "items": [
                        {
                            "source_ref": "memory:milestone-book",
                            "text": "第一次认真给他挑了一本批注过的旧书准备寄去成都",
                            "importance_bp": 8200,
                        }
                    ]
                },
            }
        },
        "expression_hard_boundaries": {
            "source_ref_aliases": {"S01": "memory:milestone-book"},
            "world_claim_source_refs": {"shared_history": ["S01"]},
        },
    }
    after = bounded_inbound_context(packet)
    after = present_dialogue_observation_timing(after)
    materials = after["inner_life_snapshot"]["materials"]
    assert [i["source_ref"] for i in materials["private_impressions"]] == [
        "impression:durable-idiom-and-promise"
    ]
    assert (
        materials["lived_moment"]["current_moment"]
        == "当下环境：女生宿舍402 · 窗外下着雨；当前状态负荷：体力偏低；已连续清醒 16 小时"
    )
    assert after["context_window"]["citable_memory_shortcuts"][0]["landmark"] is True
    assert after["dialogue_observation_timing"]["recent_30m_turn_count"] == 5


def test_bounded_inbound_context_sanitizes_ai_cliches_and_preserves_relationship_and_silence_appraisal():
    packet = {
        "inner_life_snapshot": {
            "materials": {
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "speaker": "counterpart",
                            "text": "昨晚那本书挺有意思",
                            "dialogue_id": "d1",
                            "occurred_at": "2026-10-30T14:00:00Z",
                            "continuity_reasons": ["recent"],
                        },
                        {
                            "speaker": "companion",
                            "text": "那当然 我挑了好久",
                            "dialogue_id": "d2",
                            "delivery_state": "delivered",
                            "occurred_at": "2026-10-30T14:05:00Z",
                        },
                        {
                            "speaker": "companion",
                            "text": "今天降温好冷",
                            "dialogue_id": "d3",
                            "delivery_state": "delivered",
                            "occurred_at": "2026-10-31T04:00:00Z",
                        },
                    ],
                    "volatile_last_turn": {
                        "speaker": "counterpart",
                        "text": "来了来了",
                        "dialogue_id": "d4",
                        "occurred_at": "2026-11-01T02:27:00Z",
                        "continuity_reasons": ["current_turn"],
                    },
                },
                "relationship": [
                    {
                        "stage": "close_friend",
                        "variables": {"trust_bp": 6200, "closeness_bp": 6500},
                        "temperature": {"warmth_bp": 5800, "friction_bp": 1200},
                        "hysteresis": {"internal": True},
                        "recent_authored_signals": [{"signal_code": "old"}],
                    }
                ],
                "since_he_last_spoke": {
                    "seconds": 5,
                    "prior_silence_seconds": 131220,
                    "unanswered_companion_wait_seconds": 130920,
                },
                "private_impressions": [
                    {
                        "source_ref": "imp:book",
                        "subject_ref": "user:geoff",
                        "expiry_condition": "until_counter_evidence",
                        "first_seen": "2026-10-30T14:06:00Z",
                        "last_supported": "2026-10-30T14:06:00Z",
                        "reflection_summary": "把寄书悬念那条等着的状态收拢一下：贫嘴来回我嘴上怼、心里受用。先这么记着。",
                    }
                ],
                "memory_understandings": [
                    {
                        "source_ref": "mu:book",
                        "facet": "subjective_relationship",
                        "summary": "旧书市场线收口后的余温还在：贫归贫，听着受用。下午这段安静里的等待感，是这条线淡出前最后一点余韵。",
                    }
                ],
                "appraisals": {
                    "columns": ["ref", "conf", "since", "until", "readings", "excerpts"],
                    "stable_rows": [
                        [
                            "appraisal:npc-1",
                            6800,
                            "2026-10-31T06:00:00Z",
                            "2026-11-02T06:00:00Z",
                            [["林晚那边的名单压力还在", "npc", "partly_controllable", "moderate"]],
                            [],
                        ],
                        [
                            "appraisal:npc-2",
                            6800,
                            "2026-10-31T08:00:00Z",
                            "2026-11-02T08:00:00Z",
                            [["周叙的人还在路口堵人", "npc", "partly_controllable", "moderate"]],
                            [],
                        ],
                        [
                            "appraisal:npc-3",
                            6800,
                            "2026-10-31T10:00:00Z",
                            "2026-11-02T10:00:00Z",
                            [["社团传单还在吵", "world", "partly_controllable", "low"]],
                            [],
                        ],
                    ],
                    "volatile_last_row": [
                        "appraisal:silence-wait",
                        7500,
                        "2026-10-31T20:00:00Z",
                        "2026-11-02T20:00:00Z",
                        [["隔了一整天都没回我两轮消息，心里有点失落和闷气", "user", "partly_controllable", "moderate"]],
                        [],
                    ],
                },
            }
        }
    }
    after = bounded_inbound_context(packet, include_delivery_overlap=True)
    materials = after["inner_life_snapshot"]["materials"]
    rel = materials["relationship"][0]
    assert rel["variables"] == {"trust_bp": 6200, "closeness_bp": 6500}
    assert rel["temperature"] == {"warmth_bp": 5800, "friction_bp": 1200}
    assert "hysteresis" not in rel
    assert "recent_authored_signals" not in rel
    assert materials["appraisals"]["volatile_last_row"][0] == "appraisal:silence-wait"
    assert len(materials["appraisals"]["stable_rows"]) == 2
    assert materials["since_he_last_spoke"]["prior_silence_human"] == "1天12小时"
    assert after["dialogue_observation_timing"]["unanswered_companion_turn_count"] == 2


def test_bounded_inbound_context_preserves_same_day_hour_old_companion_replies_in_conversation():
    from companion_daemon.world_v2.model_context_reading import present_context_evidence

    packet = {
        "inner_life_snapshot": {
            "contract": "inner-life-snapshot.1",
            "cursor": {"logical_time": "2026-11-02T13:47:00Z"},
            "materials": {
                "conversation": [
                    "他（3 小时前）：吃把子肉盖饭🤤",
                    "我（3 小时前）：把子肉啊 好久没吃了",
                    "我（3 小时前）：好过你在那边惦记着把子肉，我这边可能就青菜豆腐",
                    "我（12 分钟前）：刚回寝室",
                    "我（12 分钟前）：今天去外滩逛了旧书夜市",
                ],
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "speaker": "counterpart",
                            "text": "吃把子肉盖饭🤤",
                            "dialogue_id": "u-bazirou",
                            "occurred_at": "2026-11-02T10:36:00Z",
                        },
                        {
                            "speaker": "companion",
                            "text": "把子肉啊 好久没吃了",
                            "dialogue_id": "c-bazirou-1",
                            "occurred_at": "2026-11-02T10:37:00Z",
                        },
                        {
                            "speaker": "companion",
                            "text": "好过你在那边惦记着把子肉，我这边可能就青菜豆腐",
                            "dialogue_id": "c-bazirou-2",
                            "occurred_at": "2026-11-02T10:48:00Z",
                        },
                        {
                            "speaker": "companion",
                            "text": "刚回寝室",
                            "dialogue_id": "c-night-1",
                            "occurred_at": "2026-11-02T13:35:00Z",
                        },
                        {
                            "speaker": "companion",
                            "text": "今天去外滩逛了旧书夜市",
                            "dialogue_id": "c-night-2",
                            "occurred_at": "2026-11-02T13:35:30Z",
                        },
                    ],
                },
            },
        }
    }
    first_pass = present_context_evidence(packet)
    bounded = bounded_inbound_context(first_pass)
    entries = bounded["inner_life_snapshot"]["materials"]["conversation"]["entries"]
    lines = [e["line"] for e in entries]
    assert "他（3 小时前）：吃把子肉盖饭🤤" in lines
    assert "我（3 小时前）：把子肉啊 好久没吃了" in lines
    assert "我（3 小时前）：好过你在那边惦记着把子肉，我这边可能就青菜豆腐" in lines


def test_bounded_inbound_context_retains_high_salience_or_burst_impressions_and_session_perceptions():
    packet = {
        "inner_life_snapshot": {
            "materials": {
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "speaker": "counterpart",
                            "text": "第一句",
                            "dialogue_id": "d1",
                            "occurred_at": "2026-10-31T01:00:00Z",
                            "continuity_reasons": ["recent"],
                        },
                        {
                            "speaker": "counterpart",
                            "text": "紧接着第二句",
                            "dialogue_id": "d2",
                            "occurred_at": "2026-10-31T01:01:30Z",
                            "continuity_reasons": ["recent"],
                        },
                    ],
                    "volatile_last_turn": {
                        "speaker": "counterpart",
                        "text": "第三句",
                        "dialogue_id": "d3",
                        "occurred_at": "2026-10-31T01:02:00Z",
                        "continuity_reasons": ["current_turn"],
                    },
                },
                "private_impressions": [
                    {
                        "source_ref": "impression:in-burst",
                        "subject_ref": "user:geoff",
                        "expiry_condition": "until_appraisal_contradicted",
                        "first_seen": "2026-10-31T01:00:10Z",
                        "last_supported": "2026-10-31T01:00:10Z",
                        "reflection_summary": "他话还没说完，先听他往下讲",
                    },
                    {
                        "source_ref": "impression:high-salience-feeling",
                        "subject_ref": "user:geoff",
                        "expiry_condition": "until_appraisal_contradicted",
                        "confidence_bp": 8000,
                        "matters_bp": 7200,
                        "first_seen": "2026-10-30T22:00:00Z",
                        "last_supported": "2026-10-30T22:00:00Z",
                        "reflection_summary": "有点拿不准他到底是不是只把我当普通朋友，心里犯嘀咕",
                    },
                ],
                "perception": [
                    {
                        "influence_id": "perception:recent-video",
                        "accepted_at": "2026-10-31T00:15:00Z",
                        "title": "45分钟前看的舞蹈视频",
                    },
                    {
                        "influence_id": "perception:stale-yesterday",
                        "accepted_at": "2026-10-30T15:00:00Z",
                        "title": "10小时前看的旧视频",
                    },
                ],
            }
        }
    }
    after = bounded_inbound_context(packet)
    materials = after["inner_life_snapshot"]["materials"]
    assert [i["source_ref"] for i in materials["private_impressions"]] == [
        "impression:in-burst",
        "impression:high-salience-feeling",
    ]
    assert [p["influence_id"] for p in materials["perception"]] == [
        "perception:recent-video",
    ]


def test_bounded_inbound_context_preserves_silence_appraisal_and_pre_return_silence_during_return_burst():
    packet = {
        "inner_life_snapshot": {
            "materials": {
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "speaker": "counterpart",
                            "text": "我去忙会儿",
                            "dialogue_id": "d0",
                            "occurred_at": "2026-11-03T13:11:00Z",
                            "continuity_reasons": ["recent"],
                        },
                        {
                            "speaker": "companion",
                            "text": "早上好呀",
                            "dialogue_id": "c1",
                            "delivery_state": "delivered",
                            "occurred_at": "2026-11-03T23:02:00Z",
                        },
                        {
                            "speaker": "companion",
                            "text": "人呢？还在睡吗",
                            "dialogue_id": "c2",
                            "delivery_state": "delivered",
                            "occurred_at": "2026-11-04T03:02:00Z",
                        },
                        {
                            "speaker": "counterpart",
                            "text": "你这顿饭吃得挺久啊",
                            "dialogue_id": "d1",
                            "occurred_at": "2026-11-04T06:04:01Z",
                            "continuity_reasons": ["recent"],
                        },
                        {
                            "speaker": "companion",
                            "text": "你还好意思说我",
                            "dialogue_id": "c3",
                            "delivery_state": "delivered",
                            "occurred_at": "2026-11-04T06:04:05Z",
                        },
                    ],
                    "volatile_last_turn": {
                        "speaker": "counterpart",
                        "text": "哈哈哈哈，我昨晚通宵了",
                        "dialogue_id": "d2",
                        "occurred_at": "2026-11-04T06:04:22Z",
                        "continuity_reasons": ["current_turn"],
                    },
                },
                "since_he_last_spoke": {
                    "seconds": 2,
                    "prior_silence_seconds": 21,
                },
                "appraisals": {
                    "columns": ["ref", "conf", "since", "until", "readings", "excerpts"],
                    "stable_rows": [],
                    "volatile_last_row": [
                        "appraisal:silence-resentment",
                        7800,
                        "2026-11-04T05:02:00Z",
                        "2026-11-05T05:02:00Z",
                        [["发了好几轮都没人理，心里挺闷也有些窝火", "user", "partly_controllable", "moderate"]],
                        [],
                    ],
                },
            }
        }
    }
    after = bounded_inbound_context(packet, include_delivery_overlap=True)
    materials = after["inner_life_snapshot"]["materials"]
    assert materials["appraisals"]["volatile_last_row"][0] == "appraisal:silence-resentment"
    assert materials["since_he_last_spoke"]["silence_before_return_human"] == "16小时53分"
    timing = after["dialogue_observation_timing"]
    assert timing["silence_before_return_human"] == "16小时53分"
    assert timing["unanswered_companion_turns_before_return"] == 2
    assert timing["unanswered_wait_before_return_human"] == "7小时2分"


def test_bounded_inbound_context_deduplicates_working_memory_prefetch_and_enriches_bitemporal_facts():
    packet = {
        "current_trigger_message": {"text": "你在忙啥呢"},
        "inner_life_snapshot": {
            "cursor": {"logical_time": "2026-11-08T12:00:00+08:00"},
            "materials": {
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "speaker": "counterpart",
                            "text": "我昨晚熬夜改方案",
                            "dialogue_id": "d-visible",
                            "occurred_at": "2026-11-08T11:55:00+08:00",
                        }
                    ],
                    "volatile_last_turn": {
                        "speaker": "counterpart",
                        "text": "你在忙啥呢",
                        "dialogue_id": "d-now",
                        "occurred_at": "2026-11-08T12:00:00+08:00",
                    },
                },
                "relevant_facts": [
                    {
                        "source_ref": "fact:transient-situation",
                        "predicate_code": "situation.recent",
                        "source_excerpt": "前天晚上在赶社团海报",
                        "occurred_at": "2026-11-06T21:00:00+08:00",
                    },
                    {
                        "source_ref": "fact:durable-home",
                        "predicate_code": "location.hometown",
                        "source_excerpt": "老家在烟台",
                        "occurred_at": "2026-11-01T10:00:00+08:00",
                    },
                ],
                "appraisals": {
                    "columns": ["ref", "conf", "since", "until", "readings", "excerpts"],
                    "stable_rows": [
                        [
                            "appraisal:active-1",
                            7200,
                            "2026-11-08T12:00:00+08:00",
                            "2026-11-09T11:55:00+08:00",
                            [["他随口问我在忙什么", "user", "controllable", "low"]],
                            [],
                        ]
                    ],
                },
                "automatic_prefetch": {
                    "items": [
                        {
                            "source_ref": "dialogue-episode:bare-emoji",
                            "source_slice": "recent_dialogue",
                            "text": "一段聊天：\n[2026-11-06T12:12+08:00 user:1]: 👀",
                        },
                        {
                            "source_ref": "fact:transient-situation",
                            "source_slice": "relevant_facts",
                            "text": "前天晚上在赶社团海报",
                        },
                        {
                            "source_ref": "appraisal:active-1",
                            "source_slice": "recalled_emotional_associations",
                            "text": "Remembered private interpretation — 他随口问我在忙什么 / user / controllable / low",
                        },
                        {
                            "source_ref": "appraisal:older-recalled",
                            "source_slice": "recalled_emotional_associations",
                            "text": "Remembered private interpretation — 他专门问我昨晚睡得怎么样，是在顺着前面的话关心我的状态。 / unknown / partly_controllable / moderate",
                        },
                    ]
                },
            },
        },
    }
    after = bounded_inbound_context(packet)
    materials = after["inner_life_snapshot"]["materials"]
    facts = {f["source_ref"]: f for f in materials["relevant_facts"]}
    assert facts["fact:transient-situation"]["occurred_age"] == "2天前"
    assert facts["fact:transient-situation"]["temporal_scope"] == "point_in_time_situation"
    assert facts["fact:transient-situation"]["temporal_note"] == "2天前当时的动态/处境，非今日现状"
    assert facts["fact:durable-home"]["occurred_age"] == "7天前"
    assert facts["fact:durable-home"]["temporal_scope"] == "durable_trait_or_identity"
    assert "temporal_note" not in facts["fact:durable-home"]

    auto_items = materials["automatic_prefetch"]["items"]
    assert [item["source_ref"] for item in auto_items] == ["appraisal:older-recalled"]
    assert auto_items[0]["text"] == "他专门问我昨晚睡得怎么样，是在顺着前面的话关心我的状态。"


def test_stage_posture_understanding_flows_into_lived_moment_and_current_inner_moment():
    from datetime import datetime, timezone
    from companion_daemon.world_v2.character_interior.contracts import _rendered_lived_moment
    from companion_daemon.world_v2.model_context_reading import present_context_evidence

    logical_time = datetime(2026, 11, 8, 4, 0, tzinfo=timezone.utc)
    raw_materials = {
        "memory_understandings": [
            {
                "source_ref": "understanding:habit-1",
                "facet": "private_self",
                "summary": "最近手头紧加上忙社团，习惯傍晚去丽娃河边散步顺便盘算开销。",
                "status": "active",
            },
            {
                "source_ref": "understanding:stance-1",
                "facet": "expression_stance",
                "summary": "对他还留着三分嘴硬，但他真关心我时会忍不住说实话。",
                "status": "active",
            },
        ]
    }
    lived = _rendered_lived_moment(raw_materials, logical_time, actor_ref="agent:companion")
    assert lived is not None
    assert "近期活法与心气：" in lived
    packet = {
        "current_trigger_message": {"text": "今晚有空聊聊吗"},
        "inner_life_snapshot": {
            "cursor": {"logical_time": logical_time.isoformat()},
            "materials": {
                **raw_materials,
                "lived_moment": lived,
            },
        },
    }
    presented = present_context_evidence(packet)
    bounded = bounded_inbound_context(presented)
    cur_inner = bounded["current_trigger_message"].get("current_inner_moment", "")
    assert "近期活法与心气：" in cur_inner
    assert "丽娃河边散步" in cur_inner


def test_unshared_coordinate_and_full_understanding_tail_surface_in_lived_moment_and_shortcuts():
    from datetime import datetime, timezone
    from companion_daemon.world_v2.character_interior.contracts import _rendered_lived_moment
    from companion_daemon.world_v2.model_context_reading import present_context_evidence

    logical_time = datetime(2026, 11, 8, 4, 0, tzinfo=timezone.utc)
    aspirations_tail = "所以这段时间心里一直绷着根弦，精力被兼职和社团两头扯着，说话也没平时那么轻松。"
    raw_materials = {
        "recent_dialogue": {
            "stable_turns": [
                {
                    "speaker": "companion",
                    "text": "这周家里生活费减半，昨晚半夜还有人敲林晚的门核名单。",
                    "dialogue_id": "d-comp-1",
                    "occurred_at": "2026-11-07T14:00:00Z",
                }
            ],
            "volatile_last_turn": {
                "speaker": "counterpart",
                "text": "你们社团换届和勤工助学怎么样了？",
                "dialogue_id": "d-user-2",
                "occurred_at": "2026-11-08T04:00:00Z",
            },
        },
        "biographical_context": {
            "settled_biographical_coordinates": [
                {
                    "coordinate_ref": "biographical-coordinate:coord:allowance",
                    "summary": "家里突发变故导致生活费减半",
                    "privacy_class": "contextual",
                },
                {
                    "coordinate_ref": "biographical-coordinate:coord:club-workstudy",
                    "summary": "文学社换届盯场与学工办305勤工助学排班冲突",
                    "privacy_class": "contextual",
                },
            ]
        },
        "memory_understandings": [
            {
                "source_ref": "mu:rel-1",
                "facet": "subjective_relationship",
                "summary": "关系理解一：和他聊天能稍微松口气。",
                "status": "active",
            },
            {
                "source_ref": "mu:rel-2",
                "facet": "subjective_relationship",
                "summary": "关系理解二：虽然熟了但还是习惯先观察。",
                "status": "active",
            },
            {
                "source_ref": "mu:asp-1",
                "facet": "aspirations_conflicts",
                "summary": (
                    "家里生活费突然减半，学工办勤工助学岗位又被排进候补，社团换届活动还压在考前周末 -> "
                    + aspirations_tail
                ),
                "status": "active",
            },
        ],
    }
    lived = _rendered_lived_moment(raw_materials, logical_time, actor_ref="agent:companion")
    assert lived is not None
    assert "文学社换届盯场与学工办305勤工助学排班冲突（私下处境，未在聊天提过）" in lived
    assert "家里突发变故导致生活费减半（私下处境" not in lived
    assert aspirations_tail.rstrip("。") in lived

    packet = {
        "current_trigger_message": {"text": "你们社团换届和勤工助学怎么样了？"},
        "inner_life_snapshot": {
            "cursor": {"logical_time": logical_time.isoformat()},
            "materials": {
                **raw_materials,
                "lived_moment": lived,
            },
        },
        "expression_hard_boundaries": {
            "source_ref_aliases": {
                "S01": "biographical-coordinate:coord:allowance",
                "S02": "biographical-coordinate:coord:club-workstudy",
            },
            "world_claim_source_refs": {
                "self_report": ["S01", "S02"],
            },
            "biographical_coordinate_authority": [
                {
                    "source_ref": "S01",
                    "value": {"summary": "家里突发变故导致生活费减半"},
                },
                {
                    "source_ref": "S02",
                    "value": {"summary": "文学社换届盯场与学工办305勤工助学排班冲突"},
                },
            ],
        },
    }
    presented = present_context_evidence(packet)
    bounded = bounded_inbound_context(presented)
    shortcuts = {
        item["alias"]: item
        for item in bounded["context_window"]["citable_memory_shortcuts"]
    }
    assert shortcuts["S02"].get("shared_in_chat") is False
    assert shortcuts["S02"].get("counterpart_prior_knowledge") == "never_mentioned_in_any_prior_chat"
    assert "shared_in_chat" not in shortcuts["S01"]
    cur_msg = bounded["current_trigger_message"]
    assert "信息差提醒：" in cur_msg.get("epistemic_prior_knowledge_mismatch", "")
    assert "勤工助学" in cur_msg.get("epistemic_prior_knowledge_mismatch", "")
    assert cur_msg["current_inner_moment"].startswith("信息差提醒：")


def test_read_triggered_priority_boost_memory_tree_and_continuous_trajectory():
    from datetime import datetime
    from companion_daemon.world_v2.character_interior.contracts import _rendered_lived_moment

    logical_time = datetime.fromisoformat("2026-11-08T21:00:00+08:00")
    materials_for_moment = {
        "recently_ended_activities": [
            {
                "activity_kind": "study.self_study",
                "accepted_intention": "去二教自习室刷古代汉语习题",
                "outcome_summary": "刷完两套卷子手腕酸",
                "ended_at": "2026-11-08T16:30:00+08:00",
            },
            {
                "activity_kind": "errand.campus",
                "accepted_intention": "去学工办305交综测补审材料",
                "outcome_summary": "赶在下班前交上去了",
                "ended_at": "2026-11-08T18:10:00+08:00",
            },
        ],
    }
    lived = _rendered_lived_moment(materials_for_moment, logical_time)
    assert lived is not None
    assert "近期连续状态轨迹：今天已接连经历 2 段日常（去二教自习室刷古代汉语习题（刷完两套卷子手腕酸） → 去学工办305交综测补审材料（赶在下班前交上去了））" in lived

    packet = {
        "current_trigger_message": {"text": "你今天都在忙啥呢，还记得嘉兴包饺子那次吗"},
        "inner_life_snapshot": {
            "cursor": {"logical_time": logical_time.isoformat()},
            "materials": {
                "lived_moment": lived,
                "recent_dialogue": {
                    "stable_turns": [],
                    "volatile_last_turn": {
                        "speaker": "counterpart",
                        "text": "你今天都在忙啥呢",
                        "recorded_at": "2026-11-08T21:00:00+08:00",
                    },
                },
                "selected_recall": {
                    "items": [
                        {
                            "source_ref": "prehistory-story:jiaxing-dumplings",
                            "text": "小时候在嘉兴过年跟妈妈包饺子，把硬币包进了破皮的那只里",
                            "occurred_to": "2025-01-28T20:00:00+08:00",
                        }
                    ]
                },
                "automatic_prefetch": {
                    "items": [
                        {
                            "source_ref": "exp:yesterday-rain",
                            "source_slice": "recent_self_experiences",
                            "text": "昨天傍晚从图书馆出来忘带伞，淋湿了半边袖子",
                            "occurred_to": "2026-11-07T18:30:00+08:00",
                        },
                        {
                            "source_ref": "imp:recalled-low-bp",
                            "source_slice": "private_impressions",
                            "text": "他上次顺口提了一句想来成都看银杏",
                            "importance_bp": 5200,
                            "recall_count": 2,
                            "occurred_to": "2026-11-06T12:00:00+08:00",
                        },
                    ]
                },
                "recent_self_experiences": {
                    "items": [
                        {
                            "source_ref": "exp:today-errand",
                            "text": "下午去学工办305把综测补审材料交上去了",
                            "occurred_to": "2026-11-08T18:10:00+08:00",
                        }
                    ]
                },
            },
        },
        "expression_hard_boundaries": {
            "source_ref_aliases": {
                "S01": "exp:yesterday-rain",
                "S02": "imp:recalled-low-bp",
                "S03": "prehistory-story:jiaxing-dumplings",
                "S04": "exp:today-errand",
            },
            "world_claim_source_refs": {
                "past_world": ["S01", "S02", "S03", "S04"],
            },
        },
    }
    bounded = bounded_inbound_context(packet)
    auto_items = bounded["inner_life_snapshot"]["materials"]["automatic_prefetch"]["items"]
    # imp:recalled-low-bp has importance_bp=5200 (<6500) and is older than latest_settled_cp,
    # but recall_count=2 protects it from pruning and boosts it to the front!
    assert [it["source_ref"] for it in auto_items] == ["imp:recalled-low-bp", "exp:yesterday-rain"]

    shortcuts = bounded["context_window"]["citable_memory_shortcuts"]
    # Boosted S02 and selected_recall S03 are promoted to the front of citable_memory_shortcuts
    assert [s["alias"] for s in shortcuts[:2]] == ["S02", "S03"]

    tree = bounded["context_window"]["memory_tree"]
    assert any(item.startswith("[S03]") for item in tree["layer_1_front"])
    assert any(item.startswith("[S04] (今天)") for item in tree["layer_1_front"])
    assert any(item.startswith("[S01] (昨天)") for item in tree["layer_2_recent"])
    assert "近期连续状态轨迹：" in bounded["current_trigger_message"]["current_inner_moment"]


def test_chat_disclosure_ledger_and_spontaneous_prehistory_flash():
    import json
    from datetime import datetime
    from types import SimpleNamespace
    from companion_daemon.world_v2.character_interior.contracts import (
        _rendered_lived_moment,
        chat_disclosure_ledger_scope,
        compile_chat_disclosure_ledger,
    )

    logical_time = datetime.fromisoformat("2026-11-08T12:00:00+08:00")
    proposal_json = json.dumps(
        {
            "proposal_id": "prop:1",
            "proposed_changes": [
                {
                    "kind": "expression_plan_transition",
                    "payload": {
                        "canonical_json": json.dumps(
                            {
                                "world_claims": [
                                    {
                                        "claim_text": "林晚和周叙把下周末的读书会筹备直接派给我牵头了。",
                                        "scope": "past_world",
                                        "source_refs": [
                                            "biography-coordinate:event:world-life:settled:world:1:occurrence:club-election"
                                        ],
                                    },
                                    {
                                        "claim_text": "大一那次跟周祺办闲置交换，当天少了张桌子还临时改了摆放图。",
                                        "scope": "past_world",
                                        "source_refs": [
                                            "prehistory-record:celia-expanded-university-u14:window:0:600"
                                        ],
                                    },
                                ]
                            }
                        )
                    },
                }
            ],
        }
    )
    fake_projection = SimpleNamespace(
        world_id="world:test-disclosure",
        world_revision=42,
        deliberation_revision=10,
        ledger_sequence=100,
        execution_receipts=(
            SimpleNamespace(action_id="act:1", observed_state="delivered"),
        ),
        stored_message_payloads={
            "payload:1": "读书会那事林晚他们直接摊派给我了，跟以前办交换活动那次一样乱。"
        },
        expression_plan_manifests=(
            SimpleNamespace(
                proposal_id="prop:1",
                beats=(SimpleNamespace(action_id="act:1", payload_ref="payload:1"),),
            ),
        ),
        minimal_reply_manifests=(),
        proposal_audits=(
            SimpleNamespace(proposal_id="prop:1", proposal_json=proposal_json),
        ),
    )
    ledger = compile_chat_disclosure_ledger(fake_projection)
    assert "读书会那事林晚他们直接摊派给我了，跟以前办交换活动那次一样乱。" in ledger.all_companion_texts
    assert "occurrence:club-election" in ledger.disclosed_claims_by_ref
    assert "prehistory-record:celia-expanded-university-u14" in ledger.disclosed_claims_by_ref

    # Even when recent_dialogue has rolled past the old turn, chat_disclosure_ledger_scope
    # recognizes that club-election and u14 were already shared, while work-study and u10 were not.
    raw_materials = {
        "recent_dialogue": [
            {"speaker": "companion", "text": "刚下课，准备去吃午饭。"},
            {"speaker": "counterpart", "text": "你们社团读书会和勤工助学那边怎么样了？"},
        ],
        "biographical_context": {
            "settled_biographical_coordinates": [
                {
                    "coordinate_ref": "occurrence:club-election",
                    "settlement_event_ref": "event:world-life:settled:world:1:occurrence:club-election",
                    "category": "campus",
                    "summary": "文学社换届碰头把下周末读书会筹备直接压给沈知栀。",
                },
                {
                    "coordinate_ref": "occurrence:work-study",
                    "settlement_event_ref": "event:world-life:settled:world:1:occurrence:work-study",
                    "category": "campus",
                    "summary": "学工办305勤工助学岗只剩两个候补名额。",
                },
            ]
        },
        "automatic_prefetch": {
            "items": [
                {
                    "source_ref": "prehistory-record:celia-expanded-university-u10:window:0:400",
                    "text": "[事件发生时间 2025-04-01] 第二次交换活动前，知栀答应确认几个人会不会来，当天有两个人没出现，桌子一侧空着。",
                    "prehistory": {"story": {"record_ref": "prehistory-record:celia-expanded-university-u10"}},
                },
                {
                    "source_ref": "prehistory-record:celia-expanded-university-u14:window:0:600",
                    "text": "[事件发生时间 2025-12-01] 十二月，第三次交换按此前商量的分工准备，活动当天早上原先说好能借的桌子少了一张。",
                    "prehistory": {"story": {"record_ref": "prehistory-record:celia-expanded-university-u14"}},
                },
            ]
        },
    }
    with chat_disclosure_ledger_scope(fake_projection):
        lived = _rendered_lived_moment(raw_materials, logical_time, actor_ref="agent:companion")
        assert lived is not None
        assert "文学社换届碰头把下周末读书会筹备直接压给沈知栀（此前已在聊天提过）" in lived
        assert "学工办305勤工助学岗只剩两个候补名额（私下处境，未在聊天提过）" in lived
        assert "脑海顺带闪过的旧事：第二次交换活动前，知栀答应确认几个人会不会来" in lived
        assert "过往经历，从未在聊天提过；仅心头自然闪回，是否顺势提起完全由你决定" in lived

        packet = {
            "current_trigger_message": {
                "text": "你们社团读书会和勤工助学那边怎么样了？",
                "current_inner_moment": lived,
            },
            "inner_life_snapshot": {
                "world_id": "world:test-disclosure",
                "cursor": {
                    "world_revision": 42,
                    "deliberation_revision": 10,
                    "ledger_sequence": 100,
                    "logical_time": logical_time.isoformat(),
                },
                "materials": raw_materials,
            },
            "expression_hard_boundaries": {
                "source_ref_aliases": {
                    "S01": "biography-coordinate:event:world-life:settled:world:1:occurrence:club-election",
                    "S02": "biography-coordinate:event:world-life:settled:world:1:occurrence:work-study",
                    "S03": "prehistory-record:celia-expanded-university-u10:window:0:400",
                    "S04": "prehistory-record:celia-expanded-university-u14:window:0:600",
                },
                "world_claim_source_refs": {
                    "past_world": ["S01", "S02", "S03", "S04"],
                },
                "biographical_coordinate_authority": [
                    {
                        "source_ref": "biography-coordinate:event:world-life:settled:world:1:occurrence:club-election",
                        "value": {
                            "coordinate_ref": "occurrence:club-election",
                            "summary": "文学社换届碰头把下周末读书会筹备直接压给沈知栀。",
                        },
                    },
                    {
                        "source_ref": "biography-coordinate:event:world-life:settled:world:1:occurrence:work-study",
                        "value": {
                            "coordinate_ref": "occurrence:work-study",
                            "summary": "学工办305勤工助学岗只剩两个候补名额。",
                        },
                    },
                ],
            },
        }
        bounded = bounded_inbound_context(packet)
        shortcuts = {
            s["alias"]: s
            for s in bounded["context_window"]["citable_memory_shortcuts"]
        }
        assert shortcuts["S01"]["shared_in_chat"] is True
        assert "林晚和周叙把下周末的读书会筹备直接派给我牵头了" in shortcuts["S01"]["previously_shared_claim"]
        assert shortcuts["S02"]["shared_in_chat"] is False
        assert shortcuts["S03"]["shared_in_chat"] is False
        assert shortcuts["S04"]["shared_in_chat"] is True
        assert "大一那次跟周祺办闲置交换" in shortcuts["S04"]["previously_shared_claim"]
        mismatch = bounded["current_trigger_message"]["epistemic_prior_knowledge_mismatch"]
        assert "勤工助学" in mismatch
        assert "读书会" not in mismatch











