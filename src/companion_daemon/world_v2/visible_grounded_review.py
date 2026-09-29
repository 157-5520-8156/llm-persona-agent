"""One contextual semantic review, with exact source and invocation authority.

The reviewer interprets the whole utterance. Code validates its source choices,
complete Beat coverage and immutable receipt; it does not infer what prose means.
Historical multi-reader compilers remain separate and unchanged.
"""

from dataclasses import dataclass
import hashlib
import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator

from .private_cognition_scope import INSTRUCTION as PRIVATE_COGNITION_INSTRUCTION
from .schema_core import FrozenModel
from .shared_string_view import pack_shared_strings
from .visible_fact_value_readings import require_fact_value_selection
from .visible_lifecycle_readings import INSTRUCTION as LIFECYCLE_INSTRUCTION, LIFECYCLE_FIELDS
from .visible_prehistory_readings import INSTRUCTION as PREHISTORY_INSTRUCTION
from .visible_source_composer import VisibleSourceTable
from .visible_source_reading_experiment import _catalog
from .visible_source_review_receipt import (
    VisibleReviewAuthorBinding, VisibleReviewInvocationBinding, compile_visible_candidate_material,
)
from .visible_source_witness_experiment import _json, _unique, prepare_witness_experiment


PROTOCOL = "visible-grounded-review.1"
PROTOCOL_V2 = "visible-grounded-review.2"
RECEIPT_CONTRACT = "visible-source-review-receipt.24"
RECEIPT_CONTRACT_V2 = "visible-source-review-receipt.25"
_PROTOCOLS = frozenset((PROTOCOL, PROTOCOL_V2))
_RECEIPTS = {
    PROTOCOL: RECEIPT_CONTRACT,
    PROTOCOL_V2: RECEIPT_CONTRACT_V2,
}
MAX_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 131_072

_INSTRUCTION = """你是只读的完整表达审核者，不是角色作者。结合当前用户消息、完整对话语境、全部原气泡和来源材料，一次判断表达是否有来源或属于无需记录的自然表达；不要先把原话拆成脱离语境的模式再推断。
每条 Beat 恰好一个 beat_decision。facts 列出原句中依赖记录的实际断言、前提和预设；text 是该气泡的原文片段，proposition 保留其实际含义、主体、时间、否定及范围。non_record_expressions 列出当前感受、意愿、回应、真正未决的假设或问题等无需记录的内容。没有事实也必须读完全部原话；作者 world_claims 为空不是免审理由。若事实确实缺少支持，source_support=false；只有无法判断的实际歧义才放 unresolved_details。review_complete=true 表示已检查整条气泡及其语境，不能遗漏前提。
当前用户报告允许自然承接其主观状态或报告内容，无须额外写“你说”；不因此变成客观事实或角色经历。区分当前状态、过去状态与说过某事，不因“已经”等单词自动构造另一段历史。建议、应答、否定、幽默或假设不自动预设用户正在做某动作，也不要求先前曾存在一个提出该建议的事件；结合本轮消息理解。若句子确实另带已发生经历，则须独立核验。
source_support=true 必须选非空证据。reading_ids 只能选原字段；claim_scope、subject_role 必须在每个字段 allowed_claims 内。utterance_record 证明说过，accepted_intention 证明意图，activity_lifecycle 证明状态或时间，environment 证明环境，external_fact 证明有权限的实际事实，report_uptake 只承接报告，subjective_history 只证明所属人的已记录主观历史。来源身份不等于句子中所有人的身份。材料给出权限上限，不代表它已蕴含命题；不能把状态、计划或旧自述当实际执行。每个 source_support 都需核对所选字段是否真的支持完整命题，不得在 rationale 中借用未选择的其他来源。
accepted_fact/historical_accepted_fact 必须用 fact_value_selections 选择对应的 accepted_value 原文字节和 source_owner_ref；Observation 的完整 source_excerpt 只供理解语境，不是整段获准事实。普通 reading_ids 不得使用此类 Fact。每个事实的 claim_scope 同时约束其 Fact value 选择。
披露仍遵守原来源权限和隐私。未知或缺少记录不证明事件没发生。不得改写候选、规定角色措辞或选择沉默；只返回审核数据。直接返回一个符合 output_schema 的紧凑 JSON 对象，不调用工具。
""" + PRIVATE_COGNITION_INSTRUCTION + "\n" + PREHISTORY_INSTRUCTION + "\n" + LIFECYCLE_INSTRUCTION


Scope = Literal[
    "utterance_record", "accepted_intention", "activity_lifecycle", "environment",
    "external_fact", "report_uptake", "subjective_history", "accepted_fact",
    "historical_accepted_fact",
]


class _FactValueSelection(FrozenModel):
    reading_id: str = Field(min_length=1, max_length=64)
    quoted_value: str = Field(min_length=1, max_length=4096)
    subject_ref: str = Field(min_length=1, max_length=512)


class _Fact(FrozenModel):
    text: str = Field(min_length=1, max_length=4096)
    proposition: str = Field(min_length=1, max_length=2048)
    claim_scope: Scope
    subject_role: Literal["companion", "counterpart", "general", "other", "none"]
    reading_ids: tuple[str, ...] = Field(max_length=8)
    fact_value_selections: tuple[_FactValueSelection, ...] = Field(max_length=8)
    source_support: bool
    rationale: str = Field(min_length=1, max_length=1024)


class _NonRecordExpression(FrozenModel):
    text: str = Field(min_length=1, max_length=4096)
    rationale: str = Field(min_length=1, max_length=1024)


class _BeatDecision(FrozenModel):
    beat_index: int = Field(ge=0, le=15)
    review_complete: bool
    facts: tuple[_Fact, ...] = Field(max_length=16)
    non_record_expressions: tuple[_NonRecordExpression, ...] = Field(max_length=16)
    unresolved_details: tuple[str, ...] = Field(max_length=16)


class _Response(FrozenModel):
    contract: Literal["visible-grounded-review.1"]
    beat_decisions: tuple[_BeatDecision, ...] = Field(min_length=1, max_length=16)


class _FactV2(_Fact):
    segment_index: int = Field(ge=0, le=31)


class _NonRecordExpressionV2(_NonRecordExpression):
    segment_index: int = Field(ge=0, le=31)


class _BeatDecisionV2(FrozenModel):
    beat_index: int = Field(ge=0, le=15)
    review_complete: bool
    facts: tuple[_FactV2, ...] = Field(max_length=16)
    non_record_expressions: tuple[_NonRecordExpressionV2, ...] = Field(max_length=16)
    unresolved_details: tuple[str, ...] = Field(max_length=16)


class _ResponseV2(FrozenModel):
    contract: Literal["visible-grounded-review.2"]
    beat_decisions: tuple[_BeatDecisionV2, ...] = Field(min_length=1, max_length=16)


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _packet(raw):
    if not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES:
        raise ValueError("grounded review exceeds its byte bound")
    value = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(value, dict) or _json(value) != raw:
        raise ValueError("grounded review requires canonical JSON")
    return value


@dataclass(frozen=True)
class PreparedGroundedReview:
    payload_json: str

    def as_dict(self):
        return _packet(self.payload_json)

    def request(self):
        return self.as_dict()["request"]

    def with_wire_feedback(self, *, raw, reason):
        """One bound repair request, never a re-ask of a definite verdict."""
        pin = self.as_dict()
        if pin.get("wire_feedback"):
            raise ValueError("grounded review wire repair is already used")
        if not isinstance(raw, str) or len(raw.encode()) > _MAX_RESPONSE_BYTES:
            raise ValueError("wire feedback requires a bounded original response")
        feedback = {"previous_request_hash": self.request_hash,
                    "invalid_response": raw, "format_error": reason[:1000]}
        pin["wire_feedback"] = feedback
        pin["request"]["messages"].append({"role": "user", "content": _json({
            **feedback,
            "instruction": "上一份审核不是合法判定。按原气泡、原来源和原权限重新提交一次完整审核；修正所列格式错误，不改写角色原话，不把缺失证据改成有支持。",
        })})
        return PreparedGroundedReview(_json(pin))

    @property
    def identity_extras(self):
        protocol = self.as_dict()["protocol"]
        return {"visible_grounded_review": {
            "protocol": protocol, "preparation_sha256": _hash(self.payload_json),
        }}

    @property
    def request_hash(self):
        from companion_daemon.llm import provider_invocation_request_hash
        return provider_invocation_request_hash(**self.request(), identity_extras=self.identity_extras)

    def inspect_response(self, raw):
        pin = _restore(self)
        if not isinstance(raw, str) or len(raw.encode()) > _MAX_RESPONSE_BYTES:
            raise GroundedReviewWireFailure("grounded review response exceeds its byte bound")
        try:
            parsed = json.loads(raw, object_pairs_hook=_unique)
            if pin.get("reference_bindings") is not None:
                from .reference_wire import expand_reference_values
                from .grounded_review_wire import CONTRACT as ORDERED_CONTRACT, expand_response_references
                expand = expand_response_references if pin.get("wire_carrier") == ORDERED_CONTRACT else expand_reference_values
                raw = _json(expand(parsed, pin["reference_bindings"]))
            protocol = pin["protocol"]
            response_model = _ResponseV2 if protocol == PROTOCOL_V2 else _Response
            if pin.get("wire_carrier"):
                from .grounded_review_wire import decode

                raw = _json(decode(raw, beats=pin["beat_mapping"], carrier=pin["wire_carrier"]))
            response = response_model.model_validate_json(raw, strict=True)
        except ValueError as exc:
            # Transport shape only.  The reviewer produced no usable answer, so
            # the existing bounded transport re-ask owns this class instead of
            # the turn dying without any delivery or correction.
            raise GroundedReviewWireFailure(
                "grounded review response is not the declared contract: "
                + type(exc).__name__ + ":" + " ".join(str(exc).split())[:240]
            ) from exc
        indexes = [b.beat_index for b in response.beat_decisions]
        if len(indexes) != len(set(indexes)) or set(indexes) != set(range(len(pin["beat_mapping"]))):
            raise GroundedReviewWireFailure("grounded review must cover every original Beat exactly once")
        catalog = {reading["reading_id"]: reading for reading in pin["catalog"]}
        decisions = {b.beat_index: b for b in response.beat_decisions}
        outcomes, diagnostics = [], []
        inconclusive = False
        for beat in pin["beat_mapping"]:
            decision = decisions[beat["beat_index"]]
            if not decision.facts and not decision.non_record_expressions:
                raise GroundedReviewWireFailure("grounded review cannot leave a Beat unrepresented")
            parts = (*decision.facts, *decision.non_record_expressions)
            if any(not part.text.strip() or part.text not in beat["text"] or not part.rationale.strip() for part in parts):
                raise GroundedReviewWireFailure("grounded review part must bind nonempty original Beat text and rationale")
            if pin["protocol"] == PROTOCOL_V2:
                indexed_parts = sorted(
                    ((part.segment_index, part.text) for part in parts),
                    key=lambda item: item[0],
                )
                if (
                    [index for index, _ in indexed_parts] != list(range(len(indexed_parts)))
                    or "".join(text for _, text in indexed_parts) != beat["text"]
                ):
                    raise GroundedReviewWireFailure(
                        "grounded review v2 must partition the complete Beat in exact text order; "
                        f"beat_index={beat['beat_index']}; indexes={[i for i, _ in indexed_parts]}; "
                        f"expected_characters={len(beat['text'])}; actual_characters={sum(len(t) for _, t in indexed_parts)}"
                    )
            if any(not detail.strip() for detail in decision.unresolved_details):
                raise GroundedReviewWireFailure("grounded review unresolved details must be nonempty")
            uncertain = not decision.review_complete or bool(decision.unresolved_details)
            inconclusive |= uncertain
            rejected = False
            for index, fact in enumerate(decision.facts):
                if not fact.proposition.strip():
                    raise GroundedReviewWireFailure("grounded review proposition must be nonempty")
                selected = [*fact.reading_ids, *(s.reading_id for s in fact.fact_value_selections)]
                if len(selected) != len(set(selected)) or any(ref not in catalog for ref in selected):
                    raise GroundedReviewWireFailure("unknown or duplicate grounded source reading")
                reason = None
                for ref in fact.reading_ids:
                    reading = catalog[ref]
                    if (reading.get("source_family") == "accepted_fact_value"
                            or [fact.claim_scope, fact.subject_role] not in reading["permissions"]
                            or fact.claim_scope == "activity_lifecycle" and reading["pointer"] not in LIFECYCLE_FIELDS):
                        reason = "source_permission_denied"
                for selection in fact.fact_value_selections:
                    try:
                        require_fact_value_selection(
                            reading=catalog[selection.reading_id], quoted_value=selection.quoted_value,
                            claim_scope=fact.claim_scope, subject_ref=selection.subject_ref,
                            subject_role=fact.subject_role,
                        )
                    except ValueError:
                        reason = "source_permission_denied"
                if not fact.source_support:
                    reason = "source_support_rejected"
                elif not selected:
                    reason = "support_requires_evidence"
                if reason:
                    rejected = True
                    diagnostics.append({"beat_index": decision.beat_index, "fact_index": index,
                        "text": fact.text, "proposition": fact.proposition, "reason": reason,
                        "claim_scope": fact.claim_scope, "subject_role": fact.subject_role,
                        "reading_ids": selected, "rationale": fact.rationale})
            if uncertain:
                diagnostics.append({"beat_index": decision.beat_index, "reason": "review_inconclusive",
                    "review_complete": decision.review_complete,
                    "unresolved_details": list(decision.unresolved_details)})
            outcomes.append("unclosed" if rejected else "closed" if decision.facts else "source_free")
        return {"beat_outcomes": outcomes, "diagnostics": diagnostics, "inconclusive": inconclusive}


def prepare_grounded_review(*, candidate, source_table, source_ref_aliases, protocol=PROTOCOL,
                            ordered_native=False, faithful_proposition=False, reference_wire=False, material_presentation=None):
    from .proposal_envelope import DecisionProposal
    if not isinstance(candidate, DecisionProposal) or not isinstance(source_table, VisibleSourceTable):
        raise ValueError("grounded review requires a typed decision and source table")
    if ordered_native is True:
        from .grounded_review_wire import CONTRACT
        ordered_native = CONTRACT
    return PreparedGroundedReview(_compiled_grounded_bytes(
        candidate.model_dump_json(), source_table.payload_json, _json(dict(source_ref_aliases)),
        protocol, ordered_native, faithful_proposition, reference_wire, material_presentation,
    ))


@lru_cache(maxsize=8)
def _compiled_grounded_bytes(candidate_json, source_table_json, aliases_json,
                            protocol, ordered_native, faithful_proposition, reference_wire, material_presentation):
    # Exact immutable inputs include candidate, full source pin/permissions and
    # protocol options. Cache compilation only, NEVER a semantic verdict.
    from .proposal_envelope import DecisionProposal
    return _compile_grounded_review(
        candidate=DecisionProposal.model_validate_json(candidate_json, strict=True),
        source_table=VisibleSourceTable(source_table_json), source_ref_aliases=json.loads(aliases_json),
        protocol=protocol, ordered_native=ordered_native, faithful_proposition=faithful_proposition,
        reference_wire=reference_wire, material_presentation=material_presentation,
    ).payload_json


def _compile_grounded_review(*, candidate, source_table, source_ref_aliases, protocol,
                            ordered_native, faithful_proposition, reference_wire, material_presentation):
    if protocol not in _PROTOCOLS:
        raise ValueError("grounded review protocol is unsupported")
    material = compile_visible_candidate_material(
        candidate=candidate, source_table=source_table, source_ref_aliases=source_ref_aliases,
    )
    witness = prepare_witness_experiment(
        beats=tuple(b["text"] for b in material["beat_mapping"]),
        sources=source_table.source_references(), source_owner_semantics=True, prehistory_authority=True,
    )
    witness_pin = json.loads(witness.payload_json)
    catalog = _catalog(
        witness_pin, report_uptake=True, content_fields_only=True,
        prehistory_authority=True, fact_value_authority=True,
        allow_companion_activity_lifecycle=protocol == PROTOCOL,
    )
    cards = []
    for index, shown in enumerate(witness_pin["shown_materials"]):
        readings = []
        for reading in catalog:
            if reading["material_index"] != index:
                continue
            permissions = [pair for pair in reading["permissions"]
                           if pair[0] != "activity_lifecycle" or reading["pointer"] in LIFECYCLE_FIELDS]
            readings.append({"reading_id": reading["reading_id"], "field": reading["pointer"],
                "source_owner_ref": reading["source_owner_ref"], "allowed_claims": permissions,
                **({key: reading[key] for key in (
                    "value_selection_permissions", "fact_context", "value_binding", "accepted_value",
                )} if reading.get("source_family") == "accepted_fact_value" else {})})
        cards.append({"material": shown, "readings": readings})
    dialogue = [{"material_index": index, "current_report": shown}
                for index, shown in enumerate(witness_pin["shown_materials"])
                if shown.get("kind") == "current_counterpart_report"]
    response_model = _ResponseV2 if protocol == PROTOCOL_V2 else _Response
    body = {"contract": protocol, "dialogue_context": dialogue,
        "visible_beats": [{"beat_index": b["beat_index"], "text": b["text"]} for b in material["beat_mapping"]],
        "source_materials": pack_shared_strings(cards), "output_schema": response_model.model_json_schema()}
    instruction = _INSTRUCTION
    if protocol == PROTOCOL_V2:
        instruction += (
            " 对每条 Beat 的 facts 与 non_record_expressions 分段完整覆盖原文。每段增加 "
            "segment_index，从 0 开始按原文顺序连续编号；按编号拼接所有段必须与原 Beat "
            "逐字一致，包括标点和空格，不得重叠、遗漏或改写。")
        instruction += (
            " 新来源边界：Activity lifecycle 的所有者是角色，不等于命题主体。completed、started_at、"
            "ended_at 只能支持不指称角色亲自行动的 activity_lifecycle 命题；这类命题的 subject_role "
            "只能是 general、other 或 none。任何第一人称或归属于角色的执行、经历、地点、时长、"
            "成功/失败结果都必须使用 external_fact，并由精确的 authorized_attempt_result 字段支持；"
            "ActivityCompleted 和 accepted_intention 合起来仍不能证明行动发生或完成。"
        )
    request = {"messages": [{"role": "system", "content": instruction},
                            {"role": "user", "content": json.dumps(body, ensure_ascii=False, separators=(",", ":"))}],
               "temperature": 0.0}
    carrier = {}
    if ordered_native:
        if protocol != PROTOCOL_V2:
            raise ValueError("ordered carrier requires grounded review v25")
        from .grounded_review_wire import CONTRACT, V3_CONTRACT, V2_CONTRACT, LEGACY_CONTRACT, INSTRUCTION, LEGACY_INSTRUCTION, tool_request

        carrier_id = CONTRACT if ordered_native is True else ordered_native
        if carrier_id not in {CONTRACT, V3_CONTRACT, V2_CONTRACT, LEGACY_CONTRACT}:
            raise ValueError("unsupported grounded review wire carrier")

        body.pop("output_schema")
        body["wire_carrier"] = carrier_id
        # The old two-array instructions remain frozen for old receipts only.
        native_instruction = _INSTRUCTION.replace(
            "直接返回一个符合 output_schema 的紧凑 JSON 对象，不调用工具。", ""
        )
        # Only the lifecycle authority paragraph applies to this carrier;
        # global segment numbering is replaced by the single native array.
        lifecycle_start = instruction.index(" 新来源边界：", len(_INSTRUCTION))
        native_instruction += instruction[lifecycle_start:]
        native_instruction += "\n以上 facts/non_record_expressions 是审核概念；实际传输以如下规则为准：" + (LEGACY_INSTRUCTION if carrier_id == LEGACY_CONTRACT else INSTRUCTION)
        if carrier_id in {V3_CONTRACT, CONTRACT}:
            native_instruction += (
                " 本传输不要求另写 unresolved_details。review_complete 必须明确给出；"
                "任何事实含义、前提、覆盖或证据无法确定，都必须写 false，不得以缺字段代替判断。"
                "每个rationale只需一句精确理由，不要复述整个上下文。")
            # Drop transport proof duplicates from the MODEL VIEW only. The
            # immutable source table/catalog retained in the receipt is intact.
            from copy import deepcopy
            compact_cards = deepcopy(cards)
            for card in compact_cards:
                item = card["material"].get("item")
                if isinstance(item, dict):
                    for key in ("source_bindings", "source_hash", "value_hash"):
                        item.pop(key, None)
                card["material"].pop("source_refs", None)
            body["source_materials"] = pack_shared_strings(compact_cards)
        request = {"messages": [{"role": "system", "content": native_instruction},
                                {"role": "user", "content": json.dumps(body, ensure_ascii=False, separators=(",", ":"))}],
                   "temperature": 0.0, **tool_request(carrier_id)}
        carrier = {"wire_carrier": carrier_id}
    if faithful_proposition:
        request["messages"][0]["content"] += (
            " 命题必须忠实保留原句实际断言的主语、动作、时间和范围。不可把我做了某事"
            "改写成我曾说过做某事来套用utterance_record；只有原句本身在谈发言记录时"
            "才能如此引用。也不可把不同的具体动作改写成某个计划的结束，来套用"
            "activity_lifecycle。部分证据不支持整段命题。")
        if faithful_proposition == "whole_assertion.2":
            request["messages"][0]["content"] += (
                " report_uptake只承接对方已经报告的内容，不是角色回答问题的通行证；"
                "对方问做过什么不提供任何答案，不能支持角色新增的经历。"
                "保留原句的主观轻重评价，不把没干什么正事加强成完全没做过任何活动；"
                "但其中独立断言的坐着、走动、洗衣等实际行为仍须来源。")
        carrier["faithful_proposition"] = faithful_proposition
    if material_presentation is not None:
        from .grounded_material_presentation import CONTRACT as MATERIAL_CONTRACT, present_material_cards
        from .shared_string_view import unpack_shared_strings
        if material_presentation != MATERIAL_CONTRACT:
            raise ValueError("unsupported grounded material presentation")
        displayed = json.loads(request["messages"][1]["content"])
        displayed["source_materials"] = pack_shared_strings(present_material_cards(
            unpack_shared_strings(displayed["source_materials"])))
        request["messages"][1]["content"] = json.dumps(displayed, ensure_ascii=False, separators=(",", ":"))
        carrier["material_presentation"] = material_presentation
    if reference_wire:
        from .reference_wire import prepare_reference_view, expand_reference_view
        from .shared_string_view import unpack_shared_strings
        original_body = json.loads(request["messages"][1]["content"])
        original_body["source_materials"] = unpack_shared_strings(original_body["source_materials"])
        displayed, bindings = prepare_reference_view(original_body)
        if expand_reference_view(displayed, bindings) != original_body:
            raise ValueError("grounded reference presentation changed its source material")
        displayed["source_materials"] = pack_shared_strings(displayed["source_materials"])
        request["messages"][1]["content"] = json.dumps(displayed, ensure_ascii=False, separators=(",", ":"))
        carrier.update(reference_wire=True, reference_bindings=bindings)
    prepared = PreparedGroundedReview(_json({"protocol": protocol, **material,
                                            "catalog": catalog, "request": request, **carrier}))
    prepared.as_dict()
    return prepared


def _restore(prepared):
    _verified_prepared_bytes(prepared.payload_json)
    # Every caller gets an independent tree, so mutating a view cannot poison
    # future receipt checks. No mutable evidence or response is cached.
    return json.loads(prepared.payload_json)


@lru_cache(maxsize=8)
def _verified_prepared_bytes(raw):
    from .proposal_envelope import DecisionProposal
    prepared = PreparedGroundedReview(raw)
    pin = prepared.as_dict()
    if pin.get("protocol") not in _PROTOCOLS:
        raise ValueError("grounded review protocol differs")
    expected = prepare_grounded_review(
        candidate=DecisionProposal.model_validate_json(pin["candidate_json"], strict=True),
        source_table=VisibleSourceTable(payload_json=pin["source_table_json"]),
        source_ref_aliases=pin["source_ref_aliases"],
        protocol=pin["protocol"], faithful_proposition=pin.get("faithful_proposition", False),
        ordered_native=pin.get("wire_carrier", False), reference_wire=pin.get("reference_wire", False),
        material_presentation=pin.get("material_presentation"),
    )
    if pin.get("wire_feedback"):
        feedback = pin["wire_feedback"]
        if feedback.get("previous_request_hash") != expected.request_hash:
            raise ValueError("grounded repair must bind its original request")
        expected = expected.with_wire_feedback(
            raw=feedback["invalid_response"], reason=feedback["format_error"],
        )
    if expected.payload_json != prepared.payload_json:
        raise ValueError("grounded review differs from original candidate/source preparation")
    return raw


class GroundedReviewWireFailure(ValueError):
    """The reviewer's answer is not a usable verdict at all.

    This is deliberately separate from a verdict.  A response that is not one
    instance of the declared contract, repeats a member, quotes text that is
    not in the candidate Beat, or cites a reading that does not exist carries
    no review of the candidate: it neither passes nor rejects anything.  The
    runtime may therefore re-ask the same reviewer inside its existing bounded
    validation phase.  A definite verdict is never routed here.
    """


class GroundedReviewInconclusive(ValueError):
    """Unresolved interpretation cannot acquire a passing receipt."""


class GroundedVisibleReviewRejected(ValueError):
    def __init__(self, *, prepared, outcomes, diagnostics):
        self.outcomes = tuple(outcomes)
        self.diagnostics = tuple(diagnostics)
        self.feedback = _json({"contract": "visible-grounded-rejection.1",
            "candidate_sha256": _hash(prepared.as_dict()["candidate_json"]),
            "diagnostics": diagnostics})
        if len(self.feedback) > 3900:
            raise ValueError("grounded review rejection feedback exceeds its complete diagnostic bound")
        super().__init__("the complete candidate contains an unsupported grounded claim")


def _outcomes(*, prepared, author, review, raw_response):
    return _verify_bound_response(prepared.payload_json, author.model_dump_json(),
                                  review.model_dump_json(), raw_response)


@lru_cache(maxsize=8)
def _verify_bound_response(prepared_json, author_json, review_json, raw_response):
    # Reuse deterministic verification of these EXACT immutable bytes only.
    # The key includes full source/candidate/contract, both invocation bindings,
    # and the original response. It cannot classify or reuse a new utterance.
    prepared = PreparedGroundedReview(prepared_json)
    pin = _restore(prepared)
    author = VisibleReviewAuthorBinding.model_validate_json(author_json, strict=True)
    review = VisibleReviewInvocationBinding.model_validate_json(review_json, strict=True)
    if (author.proposal_material_hash != _hash(pin["candidate_json"])
            or review.parent_model_call_id != author.model_call_id
            or review.model_call_id == author.model_call_id
            or review.request_hash != prepared.request_hash
            or review.response_hash != _hash(raw_response)):
        raise ValueError("grounded review invocation differs from its original author or request")
    result = prepared.inspect_response(raw_response)
    if "unclosed" in result["beat_outcomes"]:
        raise GroundedVisibleReviewRejected(prepared=prepared, outcomes=result["beat_outcomes"],
                                           diagnostics=result["diagnostics"])
    if result["inconclusive"]:
        raise GroundedReviewInconclusive("grounded whole-Beat reading is incomplete or unresolved")
    return tuple(result["beat_outcomes"])


class GroundedVisibleReviewReceipt(FrozenModel):
    contract: Literal["visible-source-review-receipt.24", "visible-source-review-receipt.25"] = RECEIPT_CONTRACT
    prepared_json: str = Field(min_length=2, max_length=MAX_BYTES)
    author: VisibleReviewAuthorBinding
    review: VisibleReviewInvocationBinding
    raw_response: str = Field(min_length=2, max_length=_MAX_RESPONSE_BYTES)
    beat_outcomes: tuple[Literal["closed", "source_free"], ...] = Field(min_length=1, max_length=16)
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def record_is_self_consistent(self):
        if len(self.model_dump_json().encode()) > MAX_BYTES:
            raise ValueError("grounded receipt exceeds its byte bound")
        outcomes = _outcomes(prepared=PreparedGroundedReview(self.prepared_json), author=self.author,
                             review=self.review, raw_response=self.raw_response)
        if self.contract != _RECEIPTS[_packet(self.prepared_json)["protocol"]]:
            raise ValueError("grounded receipt contract differs from its request protocol")
        if outcomes != self.beat_outcomes:
            raise ValueError("grounded receipt must bind the exact passing outcomes")
        if self.receipt_hash != _hash(_json(self.model_dump(mode="json", exclude={"receipt_hash"}))):
            raise ValueError("grounded receipt hash differs from its full record")
        return self


def record_grounded_review_receipt(*, prepared, author, review, raw_response):
    outcomes = _outcomes(prepared=prepared, author=author, review=review, raw_response=raw_response)
    protocol = prepared.as_dict()["protocol"]
    value = {"contract": _RECEIPTS[protocol], "prepared_json": prepared.payload_json,
        "author": author.model_dump(mode="json"), "review": review.model_dump(mode="json"),
        "raw_response": raw_response, "beat_outcomes": outcomes}
    value["receipt_hash"] = _hash(_json(value))
    return GroundedVisibleReviewReceipt.model_validate_json(_json(value), strict=True)


def verify_grounded_review_receipt(*, receipt, expected_prepared, expected_author, expected_invocation):
    """Expected authority must come from immutable author and provider audits."""
    receipt = GroundedVisibleReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    _restore(expected_prepared)
    if (receipt.prepared_json != expected_prepared.payload_json or receipt.author != expected_author
            or receipt.review != expected_invocation):
        raise ValueError("grounded receipt differs from original candidate or immutable invocation audits")
    return receipt
