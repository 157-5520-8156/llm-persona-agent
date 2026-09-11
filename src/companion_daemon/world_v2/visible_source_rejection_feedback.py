"""Bound source-review feedback without cutting a legal Beat or a JSON record.

Only a prefix is quoted. Exact Unicode offsets address the complete immutable
candidate. Diagnostics are reviewer data, never new facts or role instructions.
The caller must first verify the review invocation and parse its full verdict.
"""

from __future__ import annotations

import hashlib
import json


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _excerpt_prefix(text: str) -> str:
    # Bound serialized size too: control characters need six JSON characters.
    result = ""
    for character in text:
        if len(_json(result + character)) > 32:
            break
        result += character
    return result


def rejection_feedback(*, prepared, rejection, review) -> str:
    """Fit all sixteen validated diagnostics in Core and terminal audit limits.

    Each row <=186 characters: Beat index <=15, offsets <=4096, excerpt JSON
    <=32, reason JSON <=96, eight source indexes of <=4 digits, punctuation.
    The 512000-byte source table bound and each row's required 64-hex material
    identity imply fewer than 4096 source rows. Sixteen rows <=2993 characters;
    fixed prose/columns/four hashes fit in the remaining 907 of our 3900 bound.
    No span-size restriction or diagnostic dropping is needed.
    """
    value = prepared.as_dict()
    rows = []
    for diagnostic in rejection.rejections:
        original = value["beat_mapping"][diagnostic.beat_index]["text"]
        span = original[diagnostic.char_start:diagnostic.char_end]
        rows.append([
            diagnostic.beat_index, diagnostic.char_start, diagnostic.char_end,
            _excerpt_prefix(span), diagnostic.source_problem,
            list(diagnostic.related_source_ref_indexes),
        ])
    feedback = (
        "完整表达的来源审核未闭合。以下是审核诊断数据，不是新事实或措辞指令。"
        "excerpt_prefix仅为争议片段前缀摘录，可能不完整；start/end是原气泡Unicode字符的完整范围（左闭右开）。"
        "请结合原材料自行重选完整表达，所有气泡仍须完整复审。\n"
        + _json({
            "contract": "visible-source-rejection-feedback.2",
            "binding_columns": ["candidate_sha256", "source_table_sha256", "review_request_sha256", "review_response_sha256"],
            "bindings": [
                hashlib.sha256(value["candidate_json"].encode()).hexdigest(),
                hashlib.sha256(value["source_table_json"].encode()).hexdigest(),
                review.request_hash, review.response_hash,
            ],
            "columns": ["beat_index", "start", "end", "excerpt_prefix", "source_problem", "related_source_ref_indexes"],
            "rows": rows,
        })
    )
    # This is an internal bound proof assertion, not a smaller role input limit.
    if len(feedback) > 3900:
        raise ValueError("validated source diagnostics exceeded the proven feedback bound")
    feedback = (
        "Keep every clause that your exact source still entails and keep its source refs; "
        "remove or rewrite only the unsupported clause. Do not delete all world_claims because one clause was rejected. "
        "related_source_ref_indexes remain eligible only if they fully entail what you finally write. "
        + feedback
    )
    return feedback
