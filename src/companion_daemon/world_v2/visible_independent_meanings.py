"""Source probes for two independently produced readings of the same Beats.

All propositions remain separate, even when they sound equivalent. This avoids
letting either reader's interpretation become the other reader's instructions.
The caller must prove the actual independent invocations; these diagnostics
alone cannot approve a candidate, source-free speech, receipts, or Actions.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from .visible_candidate_meaning import PreparedCandidateMeaning, verify_candidate_meaning_preparation
from .visible_meaning_source_review import (
    _eligible_readings, _validation, prepare_meaning_source_review,
)
from .visible_source_witness_experiment import _json, _unique

CONTRACT = "visible-independent-meanings-source-probe.1"
SHARED_STRING_CONTRACT = "visible-independent-meanings-source-probe.2"
CONTENT_FIELD_CONTRACT = "visible-independent-meanings-source-probe.3"
PREHISTORY_CONTRACT = "visible-independent-meanings-source-probe.4"
CONTENT_FIELD_CONTRACTS = frozenset((CONTENT_FIELD_CONTRACT, PREHISTORY_CONTRACT))


@dataclass(frozen=True)
class IndependentMeaning:
    preparation: PreparedCandidateMeaning
    raw_response: str


def _readings(meanings: tuple[IndependentMeaning, IndependentMeaning]) -> tuple[list[dict], list[str]]:
    if len(meanings) != 2:
        raise ValueError("independent meaning probe requires exactly two readings")
    packets = [verify_candidate_meaning_preparation(m.preparation) for m in meanings]
    if packets[0]["beats"] != packets[1]["beats"]:
        raise ValueError("independent readings must cover the same original Beats")
    return [m.preparation.inspect_response(m.raw_response) for m in meanings], packets[0]["beats"]


@dataclass(frozen=True)
class PreparedIndependentMeanings:
    payload_json: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload_json.encode()).hexdigest()

    def request(self) -> dict:
        return json.loads(self.payload_json)["request"]

    def inspect_response(self, raw: str) -> dict:
        pin = json.loads(self.payload_json, object_pairs_hook=_unique)
        meanings = tuple(IndependentMeaning(PreparedCandidateMeaning(m["preparation_json"]), m["raw_response"])
                         for m in pin["meanings"])
        if pin["contract"] not in {CONTRACT, SHARED_STRING_CONTRACT, *CONTENT_FIELD_CONTRACTS}:
            raise ValueError("unsupported independent source presentation")
        expected = prepare_independent_meanings_sources(meanings=meanings, sources=tuple(pin["sources"]),
                                                        shared_strings=pin["contract"] in {SHARED_STRING_CONTRACT, *CONTENT_FIELD_CONTRACTS},
                                                        content_fields_only=pin["contract"] in CONTENT_FIELD_CONTRACTS,
                                                        prehistory_authority=pin["contract"] == PREHISTORY_CONTRACT)
        if expected is None or expected.payload_json != self.payload_json:
            raise ValueError("independent source preparation differs from its fixed compilation")
        value = _validation(raw, pin["request"]["tools"][0]["function"]["parameters"])
        returned = value["fact_decisions"]
        ids = [d["fact_id"] for d in returned]
        if len(set(ids)) != len(ids) or set(ids) != {f["fact_id"] for f in pin["facts"]}:
            raise ValueError("every independently read fact needs exactly one decision")
        by_id = {d["fact_id"]: d for d in returned}
        catalog = {r["reading_id"]: r for r in pin["catalog"]}
        decisions = []
        for fact in pin["facts"]:
            decision = by_id[fact["fact_id"]]
            selected = decision["reading_ids"]
            if len(set(selected)) != len(selected) or any(r not in catalog for r in selected):
                raise ValueError("unknown or duplicate evidence reading")
            allowed = _eligible_readings(fact, list(catalog.values()))
            reason = (
                "source_support_rejected" if not decision["source_support"] else
                "support_requires_evidence" if not selected else
                "source_permission_denied" if any(r not in allowed for r in selected) else None
            )
            decisions.append({
                "fact_id": fact["fact_id"], "meaning_index": fact["meaning_index"],
                "meaning_fact_id": fact["meaning_fact_id"], "beat_index": fact["beat_index"],
                "outcome": "supported" if reason is None else "rejected", "rejection_reason": reason,
                "selected_readings": [{**catalog[r], "use": "direct" if reason is None else "diagnostic_only",
                                       "permitted_scope": allowed.get(r)} for r in selected],
            })
        outcomes = []
        for index in range(len(pin["beats"])):
            facts = [d for d in decisions if d["beat_index"] == index]
            outcomes.append("facts_rejected" if any(d["outcome"] == "rejected" for d in facts)
                            else "facts_supported" if facts else "not_assessed")
        return {
            "contract": pin["contract"], "preparation_sha256": self.sha256,
            "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "fact_decisions": decisions, "fixed_fact_beat_outcomes": outcomes,
            "reviewer_response": value, "receipt_authority": False,
            "semantic_qualification": "unproven", "invocation_independence": "caller_must_verify",
            "original_candidate_qualification": "not_assessed_by_source_stage",
        }


def prepare_independent_meanings_sources(
    *, meanings: tuple[IndependentMeaning, IndependentMeaning], sources: tuple[dict, ...], shared_strings: bool = False,
    content_fields_only: bool = False, prehistory_authority: bool = False,
) -> PreparedIndependentMeanings | None:
    if type(shared_strings) is not bool:
        raise TypeError("shared string presentation flag must be boolean")
    if type(content_fields_only) is not bool or (content_fields_only and not shared_strings):
        raise ValueError("content field authority requires the shared source presentation")
    if type(prehistory_authority) is not bool or (prehistory_authority and not content_fields_only):
        raise ValueError("prehistory authority requires content field authority")
    contract = PREHISTORY_CONTRACT if prehistory_authority else CONTENT_FIELD_CONTRACT if content_fields_only else SHARED_STRING_CONTRACT if shared_strings else CONTRACT
    interpreted, beats = _readings(meanings)
    factual = [i for i, value in enumerate(interpreted) if value["facts"]]
    if not factual:
        # No probe is necessary, but this is not a source-free approval.
        return None
    if sum(len(value["facts"]) for value in interpreted) > 64:
        raise ValueError("independent meaning probe exceeds 64 factual readings")
    # Source cards are rendered inside a JSON string in the provider request.
    # Canonicalize before rendering so reopening a canonical pin reconstructs
    # the same wire bytes regardless of the caller's dictionary insertion order.
    sources = tuple(json.loads(_json(sources)))
    first = meanings[factual[0]]
    base = prepare_meaning_source_review(
        meaning=first.preparation, meaning_raw=first.raw_response, sources=sources, source_only=True,
        content_fields_only=content_fields_only, prehistory_authority=prehistory_authority,
    )
    base_pin = json.loads(base.payload_json)
    catalog = json.loads(base_pin["reading_preparation_json"])["catalog"]
    facts = [{
        **{k: v for k, v in fact.items() if k != "original_text"},
        "fact_id": f"m{index}:{fact['fact_id']}", "meaning_index": index,
        "meaning_fact_id": fact["fact_id"],
    } for index, interpretation in enumerate(interpreted) for fact in interpretation["facts"]]
    request = deepcopy(base.request())
    body = json.loads(request["messages"][1]["content"])
    body["fixed_facts"] = [{**f, "eligible_reading_ids": list(_eligible_readings(f, catalog))} for f in facts]
    body["output_contract"]["contract"] = contract
    function = request["tools"][0]["function"]
    schema = function["parameters"]
    schema["properties"]["contract"]["enum"] = [contract]
    schema["properties"]["fact_decisions"]["items"]["properties"]["fact_id"]["enum"] = [f["fact_id"] for f in facts]
    name = "review_independent_fixed_meanings_v4" if prehistory_authority else "review_independent_fixed_meanings_v3" if content_fields_only else "review_independent_fixed_meanings_v2" if shared_strings else "review_independent_fixed_meanings_v1"
    function["name"] = name
    request["tool_choice"]["function"]["name"] = name
    request["messages"][0]["content"] += (
        "命题来自两份互不参考的原句读取，meaning_index仅标记来源读取者。"
        "即使两份命题相同或相冲突，也逐条按各自固定的施事、受事、时间和类别核对世界材料；"
        "不可用另一命题作为证据、替换本命题或代替它作答。不猜哪份解释更可信，不消除分歧；"
        "每个fact_id都必须保留自己的判定。"
    )
    if shared_strings:
        from .shared_string_view import pack_shared_strings
        decision = schema["properties"]["fact_decisions"]["items"]
        del decision["properties"]["explanation"]
        decision["required"].remove("explanation")
        request["messages"][0]["content"] = request["messages"][0]["content"].replace(
            "explanation简短说明对应或缺失，避免逐项重复整段材料。",
            "每项只输出fact_id、source_support、reading_ids；不生成解释文字。",
        )
        body["source_materials"] = pack_shared_strings(body["source_materials"])
        request["messages"][0]["content"] += (
            "source_materials使用无损shared-string-view.1：value仍是完整原始材料数组，"
            "其中每个等于strings字典键的字符串都代表该键对应的完整原文；键不是新的实体或事实。"
            "相同键在所有材料中表示相同原文。先按此字典阅读材料，不能把键当成缺失内容；"
            "事实判定仍按原来的完整材料和权限进行，reading_ids保持不变。"
        )
    if content_fields_only:
        body["world_consequence_field_authority"] = {
            "contract": "world-consequence-content-field-authority.1",
            "direct_content": ["environment.text", "authorized_attempt_result.text"],
            "provenance_is_not_event_content": True,
        }
        request["messages"][0]["content"] += (
            "世界结果的事件ID、hash、版本、权限标签及执行绑定只用于核对来源身份和范围，"
            "不能补出正文未记录的动作或结果。只有目录中的正文reading可支持其实际记载的内容；"
            "正文的存在仍不等于待证命题成立。"
        )
    if prehistory_authority:
        from .visible_prehistory_readings import CONTRACT as history_contract, INSTRUCTION
        body["prehistory_field_authority"] = {
            "contract": history_contract, "direct_content": "exact_retained_text_only",
            "other_subjects": "declared_historical_participants_only",
            "provenance_is_not_event_content": True,
        }
        request["messages"][0]["content"] += INSTRUCTION
    request["messages"][1]["content"] = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return PreparedIndependentMeanings(_json({
        "contract": contract, "beats": beats,
        "meanings": [{"preparation_json": m.preparation.payload_json, "raw_response": m.raw_response} for m in meanings],
        "sources": sources, "facts": facts, "catalog": catalog, "request": request,
    }))
