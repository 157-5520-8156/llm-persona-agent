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
        expected = prepare_independent_meanings_sources(meanings=meanings, sources=tuple(pin["sources"]))
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
            "contract": CONTRACT, "preparation_sha256": self.sha256,
            "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "fact_decisions": decisions, "fixed_fact_beat_outcomes": outcomes,
            "reviewer_response": value, "receipt_authority": False,
            "semantic_qualification": "unproven", "invocation_independence": "caller_must_verify",
            "original_candidate_qualification": "not_assessed_by_source_stage",
        }


def prepare_independent_meanings_sources(
    *, meanings: tuple[IndependentMeaning, IndependentMeaning], sources: tuple[dict, ...],
) -> PreparedIndependentMeanings | None:
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
    body["output_contract"]["contract"] = CONTRACT
    function = request["tools"][0]["function"]
    schema = function["parameters"]
    schema["properties"]["contract"]["enum"] = [CONTRACT]
    schema["properties"]["fact_decisions"]["items"]["properties"]["fact_id"]["enum"] = [f["fact_id"] for f in facts]
    name = "review_independent_fixed_meanings_v1"
    function["name"] = name
    request["tool_choice"]["function"]["name"] = name
    request["messages"][0]["content"] += (
        "命题来自两份互不参考的原句读取，meaning_index仅标记来源读取者。"
        "即使两份命题相同或相冲突，也逐条按各自固定的施事、受事、时间和类别核对世界材料；"
        "不可用另一命题作为证据、替换本命题或代替它作答。不猜哪份解释更可信，不消除分歧；"
        "每个fact_id都必须保留自己的判定。"
    )
    request["messages"][1]["content"] = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return PreparedIndependentMeanings(_json({
        "contract": CONTRACT, "beats": beats,
        "meanings": [{"preparation_json": m.preparation.payload_json, "raw_response": m.raw_response} for m in meanings],
        "sources": sources, "facts": facts, "catalog": catalog, "request": request,
    }))
