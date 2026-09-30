"""Rebuild and structurally check this offline creative bundle; never import a World."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LINES = {"family": "F", "growing": "G", "university": "U"}
REQUIRED = {
    "id", "title", "period_start", "period_end", "depth", "narrative", "memory",
    "interpretation", "unknowns", "related_ids", "existing_links", "future_open",
}


def main():
    sources, scenes, errors, documents = [], [], [], {}
    for line, prefix in LINES.items():
        path = ROOT / f"{line}.json"
        raw = path.read_bytes()
        doc = json.loads(raw)
        if doc.get("format") != "creative-prehistory-candidates.1":
            errors.append(f"{line}: wrong format")
        if doc.get("status") != "unreviewed_not_imported":
            errors.append(f"{line}: wrong status")
        documents[line] = doc
        rows = doc["scenes"]
        expected = {f"{prefix}{i:02d}" for i in range(1, 21)}
        if len(rows) != 20 or {s["id"] for s in rows} != expected:
            errors.append(f"{line}: expected exactly its 20 assigned IDs")
        sources.append({"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
        for row in rows:
            sid = row["id"]
            if not REQUIRED <= row.keys():
                errors.append(f"{sid}: missing fields {REQUIRED - row.keys()}")
                continue
            start, end = date.fromisoformat(row["period_start"]), date.fromisoformat(row["period_end"])
            if not date(2005, 4, 12) <= start <= end <= date(2026, 7, 31):
                errors.append(f"{sid}: date range invalid")
            if line == "university" and start < date(2024, 9, 1):
                errors.append(f"{sid}: predates university enrollment")
            for key in ("title", "narrative", "memory"):
                if not isinstance(row[key], str) or not row[key].strip():
                    errors.append(f"{sid}: empty {key}")
            if len(row["narrative"]) > 1400:
                errors.append(f"{sid}: narrative exceeds agreed scene bound")
            if len(row["memory"]) > 1400:
                errors.append(f"{sid}: memory too long for later archive framing")
            if row["depth"] not in {"light", "medium", "deep"}:
                errors.append(f"{sid}: unknown editorial depth")
            if not isinstance(row["interpretation"], str):
                errors.append(f"{sid}: interpretation must be a string")
            for key in ("unknowns", "related_ids", "existing_links", "future_open"):
                if not isinstance(row[key], list) or any(not isinstance(v, str) for v in row[key]):
                    errors.append(f"{sid}: invalid {key}")
            scenes.append({**row, "line": line, "source": f"{line}.json#/scenes/{rows.index(row)}/narrative"})
    repo = ROOT.parents[2]
    existing_ids = set()
    for rel in ("configs/prehistory/celia-everyday-20260929.json", "fixtures/world_v2/celia_lifeline_prehistory_unreviewed.json"):
        old = json.loads((repo / rel).read_text(encoding="utf-8"))
        existing_ids.update(r["record_id"] for r in old["records"])
    for row in scenes:
        for ref in row["existing_links"]:
            if ref not in existing_ids:
                errors.append(f"{row['id']}: unknown existing record {ref}")
    ids = {s["id"] for s in scenes}
    if len(ids) != len(scenes):
        errors.append("duplicate scene ID")
    for row in scenes:
        for ref in row["related_ids"]:
            if ref not in ids or ref == row["id"]:
                errors.append(f"{row['id']}: invalid related ID {ref}")
    for key in ("narrative", "memory"):
        if len({s[key].strip() for s in scenes}) != len(scenes):
            errors.append(f"exact duplicate {key}")
    if errors:
        raise SystemExit("\n".join(errors))
    story = ["# 知栀的三段生活", "", "> 离线前史创作原文。当前审核、导入及角色保留状态见本目录README与导入审计；以下对白为记忆式创作，不是录音。", ""]
    for line, doc in documents.items():
        story.extend([f"## {doc['title']}", ""])
        for scene in doc["scenes"]:
            story.extend([f"### {scene['id']}｜{scene['title']}", "", f"时段：{scene['period_start']} 至 {scene['period_end']}。", "", scene["narrative"], ""])
        prose = [f"# {doc['title']}", "", "> 创作源稿。当前审核、导入及角色保留状态见本目录README与导入审计。对白为记忆式创作，不是录音。", "", doc["synopsis"], ""]
        for scene in doc["scenes"]:
            prose.extend([
                f"## {scene['id']}｜{scene['title']}", "",
                f"{scene['period_start']} 至 {scene['period_end']} · {scene['depth']}", "",
                scene["narrative"], "", "**记忆候选：** " + scene["memory"], "",
            ])
            if scene["interpretation"]:
                prose.extend(["**当时的理解：** " + scene["interpretation"], ""])
            if scene["unknowns"]:
                prose.extend(["**未确定：** " + "；".join(scene["unknowns"]), ""])
            if scene["related_ids"]:
                prose.extend(["**关联场景：** " + "、".join(scene["related_ids"]), ""])
        (ROOT / f"{line}.md").write_text("\n".join(prose), encoding="utf-8")
    story_text = "\n".join(story)
    (ROOT / "stories.md").write_text(story_text, encoding="utf-8")
    bundle = {
        "format": "creative-prehistory-bundle.1",
        "status": "unreviewed_not_imported",
        "scope": "60 new fictional candidate memories; not retained memories or production data",
        "sources": sources,
        "story_source": {"path": "stories.md", "sha256": hashlib.sha256(story_text.encode()).hexdigest()},
        "relation_scope": "related_ids and existing_links are editorial navigation proposals, not approved runtime causal or knowledge edges",
        "scene_count": len(scenes),
        "scenes": scenes,
    }
    packed = json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
    (ROOT / "bundle.json").write_text(packed, encoding="utf-8")
    rows = ["# 60条新增前史候选：阅读索引", "", "> 创作源稿索引。当前审核、导入及角色保留状态见本目录README与导入审计。depth仅用于编辑检查。", "", "| ID | 时段 | 故事线 | 标题 | 详略 | 关联 |", "| --- | --- | --- | --- | --- | --- |"]
    for s in sorted(scenes, key=lambda x: (x["period_start"], x["id"])):
        rows.append(f"| {s['id']} | {s['period_start']}—{s['period_end']} | {s['line']} | {s['title']} | {s['depth']} | {', '.join(s['related_ids'])} |")
    (ROOT / "index.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    result = {
        "status": "structural_checks_passed_only",
        "count": len(scenes),
        "depth_counts": dict(Counter(s["depth"] for s in scenes)),
        "narrative_characters": sum(len(s["narrative"]) for s in scenes),
        "memory_characters": sum(len(s["memory"]) for s in scenes),
        "bundle_sha256": hashlib.sha256(packed.encode()).hexdigest(),
        "limitations": ["No semantic approval", "No production import", "No role retention or recall test", "Cross-story review remains a separate human/model task"],
    }
    (ROOT / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
