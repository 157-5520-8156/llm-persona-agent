"""One same-reader structural correction over the original evidence-blind input.

A valid reading, including an inconclusive one, cannot be retried through this
port. Neither another reader's output nor source support is input to correction.
"""
from dataclasses import dataclass
import hashlib
import json

from pydantic import ValidationError

from .visible_candidate_meaning import PreparedCandidateMeaning, verify_candidate_meaning_preparation
from .visible_source_witness_experiment import _json, _unique

CONTRACT = "visible-meaning-structural-reselection.1"
MAX_BYTES = 262144


def _failure(meaning, raw):
    if not isinstance(raw, str) or len(raw.encode()) > 131072:
        raise ValueError("invalid meaning response is unavailable or exceeds correction bound")
    try:
        meaning.inspect_response(raw)
    except ValidationError as exc:
        return {"kind": "schema", "errors": exc.errors(include_input=False, include_url=False, include_context=False)}
    except json.JSONDecodeError as exc:
        return {"kind": "json_syntax", "message": exc.msg, "position": exc.pos, "line": exc.lineno, "column": exc.colno}
    except ValueError as exc:
        return {"kind": "meaning_structure", "message": str(exc)}
    raise ValueError("a structurally valid reading cannot request structural reselection")


@dataclass(frozen=True)
class PreparedMeaningReselection:
    payload_json: str

    @property
    def sha256(self):
        return hashlib.sha256(self.payload_json.encode()).hexdigest()

    def request(self):
        return json.loads(self.payload_json)["request"]

    def inspect_response(self, raw):
        pin = json.loads(self.payload_json, object_pairs_hook=_unique)
        meaning = PreparedCandidateMeaning(pin["meaning_preparation_json"])
        expected = prepare_meaning_reselection(meaning=meaning, rejected_raw=pin["rejected_raw"])
        if self.payload_json != expected.payload_json:
            raise ValueError("meaning reselection differs from original input and exact structural failure")
        return meaning.inspect_response(raw)


def prepare_meaning_reselection(*, meaning: PreparedCandidateMeaning, rejected_raw: str) -> PreparedMeaningReselection:
    verify_candidate_meaning_preparation(meaning)
    failure = _failure(meaning, rejected_raw)
    request = meaning.request()
    request["messages"][0]["content"] += (
        "\n这是同一读取器的一次结构重选。你上次的完整返回未通过结构校验。"
        "invalid_prior_reading仅为你自己的失败返回，structural_failure仅为程序报告的精确结构错误，"
        "二者均不是世界事实、其他读取者的意见或事实支持结果。请重新根据原始visible_beats完成完整读取，"
        "修正列出的格式/字段/覆盖问题，不只返回补丁。不要因为上次失败就漏掉实际命题或假装无事实；"
        "也不能把某条事实改成较容易通过的含义。保持原工具的所有字段与语义要求。"
    )
    body = json.loads(request["messages"][1]["content"])
    body.update(invalid_prior_reading=rejected_raw, structural_failure=failure)
    request["messages"][1]["content"] = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    raw = _json({"contract": CONTRACT, "meaning_preparation_json": meaning.payload_json,
                 "rejected_raw": rejected_raw, "structural_failure": failure, "request": request})
    if len(raw.encode()) > MAX_BYTES:
        raise ValueError("meaning structural reselection exceeds its full diagnostic bound")
    return PreparedMeaningReselection(raw)
