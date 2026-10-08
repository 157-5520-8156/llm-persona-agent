"""Render source-backed coverage and readable life material; no World writes."""

from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from build import load_authored, GROUPS, ROOT

ZONE = ZoneInfo("Asia/Shanghai")
CUTOFF = datetime.fromisoformat("2025-04-12T23:59:59+08:00")


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZONE)


def day(value):
    return instant(value).date().isoformat()


def render(packages: Path):
    base = json.loads((ROOT / "base-context.json").read_text())
    manifest = json.loads((packages / "manifest.json").read_text())
    authored = load_authored()
    sources = [json.loads(p.read_text()) for p in sorted(packages.glob("*/source.json"))]
    docs = [
        json.loads(p.read_text())["document"]
        for p in sorted(packages.glob("*/compiled-package.json"))
    ]
    records = {r["record_id"]: r for a in [*base["accepted_archives"], *docs] for r in a["records"]}
    source_records = {r["record_id"]: r for a in docs for r in a["records"]}
    mapping = {}
    for e in authored:
        group = next(g for g, start, end in GROUPS if start <= e["start"][:4] <= end)
        for i, _ in enumerate(e["scenes"], 1):
            pointer = f"{e['key']}.{i:02d}"
            mapping[pointer] = (
                f"prehistory-record:celia-dense20-{group}-20261008.s"
                + hashlib.sha256(pointer.encode()).hexdigest()[:24]
            )
    statistics = Counter()
    years = {
        str(y): {
            "episodes": 0,
            "scenes": 0,
            "experienced": 0,
            "told": 0,
            "world_only": 0,
            "private_interpretations": 0,
        }
        for y in range(2005, 2026)
    }
    domains = Counter()
    cast = Counter()
    dates = set()
    text_characters = 0
    for episode in authored:
        years[episode["start"][:4]]["episodes"] += 1
        domains.update(episode["domains"])
    for source in sources:
        for scene in source["scenes"]:
            y = day(scene["occurred_from"])[:4]
            years[y]["scenes"] += 1
            dates.add(day(scene["occurred_from"]))
            for block in scene["blocks"]:
                label = block["knowledge"]
                statistics[label] += 1
                text_characters += len(block["text"])
                years[y]["private_interpretations" if label == "self_reflection" else label] += 1
            cast.update(
                ref for ref in scene["participant_refs"] if ref != "history:person:celia-self"
            )
    old = list(r for a in base["accepted_archives"] for r in a["records"])
    before = [r for r in old if instant(r["occurred_until"]) <= CUTOFF]
    crossing = [
        r["record_id"]
        for r in old
        if instant(r["occurred_from"]) <= CUTOFF < instant(r["occurred_until"])
    ]
    later = [r["record_id"] for r in old if instant(r["occurred_from"]) > CUTOFF]
    weave = json.loads((ROOT / "weave.json").read_text())
    coverage = {
        "contract": "dense-twenty-year-coverage.1",
        "scope": "authored_source_not_human_memory_equivalence_or_dialogue_quality",
        "birth": "2005-04-12",
        "cutoff": CUTOFF.isoformat(),
        "dense_episodes": len(authored),
        "experienced_dense_episodes": sum(e["knowledge"] == "experienced" for e in authored),
        "early_reported_family_episodes": sum(e["knowledge"].startswith("told:") for e in authored),
        "new_scenes": sum(len(s["scenes"]) for s in sources),
        "new_readable_records": len(source_records),
        "new_knowledge_blocks": dict(statistics),
        "authored_days": len(dates),
        "new_source_characters": text_characters,
        "legacy_records": len(old),
        "legacy_wholly_before_cutoff": len(before),
        "legacy_crossing_cutoff": crossing,
        "legacy_after_cutoff": later,
        "total_records_with_legacy": len(records),
        "strictly_within_first_twenty": len(before) + len(source_records),
        "cross_archive_edges": sum(g["cross_archive_edges"] for g in manifest["groups"]),
        "edges_into_existing_baseline": sum(
            e["left"].startswith("prehistory-record:") for e in weave["edges"]
        ),
        "source_declared_native_edges": sum(g["native_edges"] for g in manifest["groups"]),
        "specific_long_stories": len(weave["story_arcs"]),
        "broad_life_threads": len(weave["legacy_members"]),
        "yearly": years,
        "author_declared_domain_episode_counts": dict(domains),
        "historical_cast_scene_counts": dict(cast),
        "automatic_retelling": "not_evaluated",
    }
    (ROOT / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n")
    (ROOT / "compiled-manifest.json").write_text((packages / "manifest.json").read_text())
    (ROOT / "life-reading.md").write_text((packages / "life-reading.md").read_text())
    # Merge actual old source text, keeping wide intervals wide and separating
    # unresolved birthday-crossing material from the first-twenty chronology.
    all_sources = [
        json.loads((ROOT.parent / "native-first-twenty-years-20261008/source.json").read_text()),
        json.loads((ROOT.parent / "native-schoolmag-episode-20261008/source.json").read_text()),
        *sources,
    ]
    entries = []
    for source in all_sources:
        for scene in source["scenes"]:
            title = scene["title"]
            start, end = scene["occurred_from"], scene["occurred_until"]
            if start is None:
                continue
            body = []
            for b in scene["blocks"]:
                prefix = (
                    "〔作者世界层，未植入〕"
                    if b["knowledge"] == "world_only"
                    else (
                        f"〔{day(b['known_from'])}后来听人讲述〕"
                        if b["knowledge"] == "told"
                        else "〔当时的私人理解〕"
                        if b["kind"] == "historical_interpretation"
                        else ""
                    )
                )
                body.append(prefix + b["text"])
            entries.append(
                (
                    instant(start),
                    f"### {day(start)}"
                    + (f"—{day(end)}" if day(start) != day(end) else "")
                    + f" · {title}",
                    body,
                )
            )
    legacy_other = []
    for archive in base["accepted_archives"][:4]:
        for r in archive["records"]:
            body = (
                [b["text"] for b in r.get("story", {}).get("blocks", [])]
                if r.get("story")
                else [r["statement"]]
            )
            heading = f"### {day(r['occurred_from'])}—{day(r['occurred_until'])} · 既有历史，保留原时间精度"
            if instant(r["occurred_until"]) <= CUTOFF:
                entries.append((instant(r["occurred_from"]), heading, body))
            else:
                legacy_other.append((heading, body))
    full = [
        "# 沈知栀：从出生到二十岁生日的生活资料合读",
        "",
        "这是新旧虚构源稿的合读本。日期区间是来源精度，不表示事件持续了整个区间；新源的日期为作者本次创作声明。",
        "婴幼儿世界层不植入主角，父母后来讲述的事保留获知日期。记录里的私人理解只属于当时。",
        "",
    ]
    year = None
    for when, heading, body in sorted(entries, key=lambda x: x[0]):
        if when.year != year:
            year = when.year
            full += [f"## {year}年", ""]
        full += [heading, "", *sum(([p, ""] for p in body), [])]
    full += [
        "## 附录：跨过生日或二十岁以后的旧记录",
        "",
        "以下继续保留在既有World中，但不计入严格截止二十岁生日的数量，也没有为凑齐二十年而改日期。",
        "",
    ]
    for heading, body in legacy_other:
        full += [heading, "", *sum(([p, ""] for p in body), [])]
    (ROOT / "twenty-year-life.md").write_text("\n".join(full) + "\n")
    guide = [
        "# 密集阶段与长期故事的交织",
        "",
        "这里是作者阅读索引，不是角色的动机表或强制回忆脚本。同一记录可以出现在多个视图，仍只有一个发生身份。",
        "",
    ]
    for name, arc in weave["story_arcs"].items():
        guide += ["## " + arc["title"], ""]
        for pointer in arc["members"]:
            rid = mapping.get(pointer, pointer)
            r = records[rid]
            text = (r.get("story") or {}).get("blocks")
            excerpt = text[0]["text"] if text else r["statement"]
            excerpt = excerpt.replace("\n", " ")
            guide.append(
                f"- {day(r['occurred_from'])}—{day(r['occurred_until'])}：{excerpt[:125]}"
                + ("…" if len(excerpt) > 125 else "")
            )
        guide.append("")
    (ROOT / "interwoven-stories.md").write_text("\n".join(guide) + "\n")
    table = [
        "# 年度覆盖",
        "",
        "| 年份 | 密集阶段起点 | 新场景 | 新亲历块 | 后来转述 | 世界层 | 私人理解 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for y, v in years.items():
        table.append(
            f"|{y}|"
            + "|".join(
                str(v[k])
                for k in (
                    "episodes",
                    "scenes",
                    "experienced",
                    "told",
                    "world_only",
                    "private_interpretations",
                )
            )
            + "|"
        )
    table += [
        "",
        "“阶段起点”按开始年份计数，场景按其发生年份计数；跨年的阶段不会把后来场景挪回前一年。",
        "",
        f"新增{coverage['new_scenes']}个场景、{coverage['new_readable_records']}条可读记录，{text_characters:,}正文字符（含标点，不含元数据）。",
        f"旧{len(old)}条中严格在二十岁生日以前的有{len(before)}条，跨生日{len(crossing)}条，之后{len(later)}条。",
        f"严格前二十年合计{coverage['strictly_within_first_twenty']}条；保留全部旧档案的World合计{len(records)}条。",
        "",
        "数量只是这份语料的规模，不证明等同真人全部记忆，也不证明普通聊天会自动选择正确片段。",
    ]
    (ROOT / "yearly-coverage.md").write_text("\n".join(table) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in coverage.items()
                if k
                not in (
                    "yearly",
                    "historical_cast_scene_counts",
                    "author_declared_domain_episode_counts",
                    "legacy_crossing_cutoff",
                    "legacy_after_cutoff",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, required=True)
    render(parser.parse_args().packages)
