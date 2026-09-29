#!/usr/bin/env python3
"""Offline reproduction of the current bounded prehistory display boundary.

Uses only synthetic text. It does not forge a reviewed archive, write World
events, or claim that the retrieval/final-author pipeline was exercised.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "output/private-audits/luna-memory-pilot-20260929"

core = "十五岁时，她和两位同学约好放学后一起去文具店。一位同学提前离开去赶公交，另一位也先走了；她在校门旁等了约十分钟，后来自己回家。没有证据说明同学是故意丢下她。她当时把先离开理解成自己不重要，觉得失落。后来她仍觉得那次等待不舒服，也知道没有证据能判断同学当时怎么想。"
middle = "她记得约定是在周四放学后，三个人原本打算先去文具店再各自回家。她在校门旁等的时候，脑中反复想着是不是自己把集合时间听错了；没有人发过消息，也没有留下对话原文。这个记忆里能够确认的动作仍然只是等了约十分钟，后来独自回家。"
tail = "多年后她把这段经验理解为：含糊约定有时会让她想早点说清时间，但这不是固定反应，她也没有因此认定朋友存心冷落她。用于边界试验的唯一中段识别细节是：她当时手里拿着一本蓝色封面的借书目录；唯一末尾识别细节是：她回家后把文具店写进了周末待办纸条。"
statement = core + middle + ("补充背景：这里只说明她后来怎样回想，不添加新的已发生情节。" * 10) + tail
limit = 480
excerpt = statement[:limit]
middle_target = "蓝色封面的借书目录"
tail_target = "周末待办纸条"
assert len(statement) > limit
result = {
    "contract": "synthetic-prehistory-excerpt-boundary.1",
    "scope": "offline string-level reproduction only; not a reviewed archive or World event",
    "statement_characters": len(statement),
    "configured_default_excerpt_characters": limit,
    "excerpt_characters": len(excerpt),
    "truncated": len(excerpt) < len(statement),
    "statement_sha256": hashlib.sha256(statement.encode()).hexdigest(),
    "excerpt_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
    "mid_detail_present_in_excerpt": middle_target in excerpt,
    "end_detail_present_in_excerpt": tail_target in excerpt,
    "recall_index_material": "RecallCorpusCompiler._document receives excerpt.text; retrieval_text adds only authorized identity labels when present",
    "retrieval_can_match_mid_or_end_detail_from_this_excerpt": middle_target in excerpt or tail_target in excerpt,
    "final_author_tested_for_this_record": False,
    "current_code_refs": [
        "src/companion_daemon/world_v2/memory_retrieval.py:370-383",
        "src/companion_daemon/world_v2/recall_corpus.py:376-414"
    ]
}
PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
PRIVATE.chmod(0o700)
target = PRIVATE / "source_read_boundary.json"
target.write_text(json.dumps(result | {"synthetic_statement": statement, "excerpt": excerpt}, ensure_ascii=False, indent=2), encoding="utf-8")
target.chmod(0o600)
print(json.dumps(result, ensure_ascii=False, indent=2))
