"""Reproduce explicitly authored scene data with the existing native compiler.

No prose generation, provider call, adoption, semantic approval or current-role
behaviour is performed here. Episode order and thread membership are author data.
"""

from __future__ import annotations
import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from companion_daemon.world_v2.native_story import NativeStorySource, compile_native_story
from companion_daemon.world_v2.prehistory_authoring import PrehistoryAuthoringBrief
from companion_daemon.world_v2.character_prehistory import digest

ROOT = Path(__file__).resolve().parent
AUTHORED = "2026-10-08T00:00:00+00:00"
GROUPS = [
    ("a", "2005", "2010"),
    ("b", "2011", "2013"),
    ("c", "2014", "2016"),
    ("d", "2017", "2019"),
    ("e", "2020", "2022"),
    ("f", "2023", "2024"),
    ("g", "2025", "2025"),
]
ALIASES = {
    "self": "celia-self",
    "mom": "shen-lan",
    "dad": "celia-father",
    "linxia": "celia-linxia",
    "jiahe": "celia-jiahe",
    "xuqiao": "celia-xuqiao",
    "zhouqi": "celia-zhouqi",
}
THREADS = {
    "home": "家里几个人各有的生活",
    "body": "身体、运动与照顾自己",
    "belonging": "一起玩、在群体里与朋友渐远",
    "making": "手、声音、文字与做不成的东西",
    "money": "想买的东西、做事与钱",
    "places": "熟悉的街区与离开嘉兴",
    "learning": "在教室内外慢慢学会的事",
    "romance": "靠近、相处与分开",
}
THREAD_ALIAS = {"voice": "making", "play": "belonging"}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_authored():
    result = []
    for path in sorted((ROOT / "authoring").glob("*.md")):
        episode = None
        for line in path.read_text().splitlines():
            if line.startswith("## "):
                fields = [p.strip() for p in line[3:].split("|")]
                if len(fields) not in (5, 6):
                    raise ValueError(f"{path}: malformed episode header")
                key, title, start, domains, threads = fields[:5]
                episode = {
                    "key": key,
                    "title": title,
                    "start": start,
                    "domains": domains.split(","),
                    "threads": [THREAD_ALIAS.get(t, t) for t in threads.split(",")],
                    "knowledge": fields[5] if len(fields) == 6 else "experienced",
                    "scenes": [],
                    "file": path.name,
                }
                result.append(episode)
            elif line and line[0].isdigit():
                if episode is None:
                    raise ValueError("scene without episode")
                offset, people, text = line.split("|", 2)
                episode["scenes"].append(
                    {"offset": int(offset), "people": people.split(","), "text": text}
                )
    if len({e["key"] for e in result}) != len(result):
        raise ValueError("duplicate episode identity")
    return result


def build(out):
    out.mkdir(parents=True, exist_ok=False)
    brief = json.loads((ROOT / "base-context.json").read_text())
    people = json.loads((ROOT / "entities.json").read_text())
    aliases = {**ALIASES, **{k: v[0] for k, v in people.items()}}
    authored = load_authored()
    weave = json.loads((ROOT / "weave.json").read_text())
    record_map = {}
    block_map = {}
    group_for = {}
    actual_dates = {}
    for episode in authored:
        year = episode["start"][:4]
        key = next(g for g, first, last in GROUPS if first <= year <= last)
        for i, scene in enumerate(episode["scenes"], 1):
            name = f"{episode['key']}.{i:02d}"
            block = "s" + hashlib.sha256(name.encode()).hexdigest()[:24]
            record_map[name] = f"prehistory-record:celia-dense20-{key}-20261008.{block}"
            block_map[name] = block
            group_for[name] = key
            actual_dates[record_map[name]] = datetime.fromisoformat(episode["start"]) + timedelta(
                days=scene["offset"]
            )
    prior_episodes = {}
    published_story_arcs = set()
    thread_members = {t: [] for t in THREADS}
    original_records = {r["record_id"]: r for a in brief["accepted_archives"] for r in a["records"]}
    reports = []
    reading = [
        "# 二十年连续生活增补阅读本",
        "",
        "婴幼儿转述与作者世界层会单独标明。以下是新增虚构源，不是当前行为要求。",
        "",
    ]
    for key, first, last in GROUPS:
        selected = [e for e in authored if first <= e["start"][:4] <= last]
        if not selected:
            continue
        source_id = f"celia-dense20-{key}-20261008"
        source = {
            "contract": "native-story-source.2",
            "source_id": source_id,
            "title": f"{first}—{last}连续生活增补",
            "authored_at": AUTHORED,
            "author_ref": "operator:codex-dense-life-source-author",
            "actor_ref": brief["actor_ref"],
            "knowledge_cutoff": "2025-04-12T23:59:59+08:00",
            "entities": [
                {"entity_ref": "history:person:" + ref, "kind": "person", "label": label}
                for ref, label in people.values()
            ],
            "storylines": [],
            "scenes": [],
            "links": [],
            "legacy_links": [],
            "open_questions": [],
            "episodes": [],
        }
        threads = sorted({t for e in selected for t in e["threads"]})
        source["storylines"] = [{"line_id": t, "title": THREADS[t]} for t in threads]
        own = set()
        for episode in selected:
            refs = []
            reading += [f"## {episode['start']} · {episode['title']}", ""]
            for i, scene in enumerate(episode["scenes"], 1):
                pointer = f"{episode['key']}.{i:02d}"
                block_id = block_map[pointer]
                date = actual_dates[record_map[pointer]].date().isoformat()
                start = date + "T00:00:00+08:00"
                end = date + "T23:59:59+08:00"
                world_only = scene["text"].startswith("[world]")
                parts = scene["text"].removeprefix("[world]").split(" [[think]] ", 1)
                body = parts[0]
                reflection = parts[1] if len(parts) == 2 else None
                mode = "world_only" if world_only else episode["knowledge"]
                informant = None
                known_from = None
                known_until = None
                if mode.startswith("told:"):
                    _, known, speaker = mode.split(":")
                    mode = "told"
                    informant = "history:person:" + aliases[speaker]
                    known_from = known + "T00:00:00+08:00"
                    known_until = known + "T23:59:59+08:00"
                block = {
                    "block_id": block_id,
                    "kind": "background" if world_only else "event",
                    "subject_actor_ref": None,
                    "text": body,
                    "knowledge": mode,
                    "informant_ref": informant,
                    "known_from": known_from,
                    "known_until": known_until,
                    "privacy_class": "private",
                }
                blocks = [block]
                if reflection is not None:
                    if mode != "experienced":
                        raise ValueError("reflection needs its own experienced scene")
                    blocks.append(
                        {
                            **block,
                            "block_id": block_id + "-thought",
                            "kind": "historical_interpretation",
                            "subject_actor_ref": brief["actor_ref"],
                            "text": reflection,
                            "knowledge": "self_reflection",
                        }
                    )
                participants = tuple(
                    dict.fromkeys(
                        ["history:person:" + aliases[a] for a in ["self", *scene["people"]]]
                    )
                )
                source["scenes"].append(
                    {
                        "event_ref": "event:dense20:"
                        + hashlib.sha256(pointer.encode()).hexdigest()[:32],
                        "title": f"{episode['title']} · {i}",
                        "occurred_from": start,
                        "occurred_until": end,
                        "line_refs": episode["threads"],
                        "participant_refs": participants,
                        "blocks": blocks,
                    }
                )
                reading += [
                    f"**{date}**"
                    + (
                        "〔作者世界层，未植入〕"
                        if world_only
                        else f"〔{known_from[:10]}后来听家人讲述，非本人亲历证明〕"
                        if mode == "told"
                        else ""
                    ),
                    body,
                    "",
                ]
                if reflection is not None:
                    reading += ["〔当时的私人理解〕" + reflection, ""]
                if not world_only:
                    refs.append(record_map[pointer])
                    own.add(pointer)
                    if reflection is not None:
                        refs.append(record_map[pointer] + "-thought")
            for extra in reversed(weave.get("episode_legacy_members", {}).get(episode["key"], [])):
                refs.insert(extra["after"], extra["record_ref"])
            if refs:
                source["episodes"].append(
                    {
                        "episode_ref": f"story-episode:celia-dense20-{episode['key']}",
                        "title": episode["title"],
                        "revision": 1,
                        "record_refs": refs,
                    }
                )
                for thread in episode["threads"]:
                    if thread not in thread_members:
                        raise ValueError("undeclared long thread " + thread)
                    thread_members[thread].append(refs[-1])
        for edge in weave["edges"]:
            left, right = edge["left"], edge["right"]
            if right not in own:
                continue
            kind = edge.get("kind", "explicit_callback")
            if left in own:
                source["links"].append(
                    {
                        "left_block_id": block_map[left],
                        "right_block_id": block_map[right],
                        "relation_kind": kind,
                    }
                )
            else:
                prior = record_map.get(left, left)
                source["legacy_links"].append(
                    {
                        "new_block_id": block_map[right],
                        "prior_record_id": prior,
                        "relation_kind": kind,
                        "old_is_left": True,
                    }
                )
        for thread, members in thread_members.items():
            if not members:
                continue
            refs = list(dict.fromkeys([*weave["legacy_members"].get(thread, []), *members]))

            def chronological(ref):
                if ref in actual_dates:
                    return actual_dates[ref]
                return (
                    datetime.fromisoformat(
                        original_records[ref]["occurred_from"].replace("Z", "+00:00")
                    )
                    .astimezone(__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))
                    .replace(tzinfo=None)
                )

            refs.sort(key=chronological)
            old = prior_episodes.get(thread)
            episode = {
                "contract": "story-episode.1",
                "episode_ref": "story-episode:celia-long-life-" + thread,
                "title": THREADS[thread],
                "revision": old["revision"] + 1 if old else 1,
                "record_refs": refs,
            }
            if old:
                episode["predecessor_hash"] = digest(old)
            source["episodes"].append(episode)
            prior_episodes[thread] = episode
        available = {r["record_id"] for a in brief["accepted_archives"] for r in a["records"]} | {
            record_map[p] for p in own
        }
        for name, arc in weave.get("story_arcs", {}).items():
            refs = [record_map.get(p, p) for p in arc["members"]]
            if name not in published_story_arcs and set(refs) <= available:
                source["episodes"].append(
                    {
                        "episode_ref": "story-episode:celia-arc-" + name,
                        "title": arc["title"],
                        "revision": 1,
                        "record_refs": refs,
                    }
                )
                published_story_arcs.add(name)
        folder = out / key
        folder.mkdir()
        source = NativeStorySource.model_validate_json(json.dumps(source, ensure_ascii=False))
        current = PrehistoryAuthoringBrief.model_validate_json(
            json.dumps(brief, ensure_ascii=False)
        )
        package = compile_native_story(
            source, brief=current, archive_id="prehistory-archive:" + source_id
        )
        write(folder / "source.json", source.model_dump(mode="json"))
        write(folder / "authoring-context.json", current.model_dump(mode="json"))
        write(folder / "compiled-package.json", package.model_dump(mode="json"))
        reports.append(
            {
                "group": key,
                "years": [first, last],
                "scenes": len(source.scenes),
                "records": len(package.document.records),
                "episodes": len(selected),
                "long_views": len(source.episodes) - len(selected),
                "published_named_arcs": sorted(published_story_arcs),
                "world_only": sum(
                    b.knowledge == "world_only" for s in source.scenes for b in s.blocks
                ),
                "source_hash": digest(source),
                "manifest_hash": digest(package.document.manifest()),
                "context_chars": len(package.compilation.context_json),
                "package_bytes": (folder / "compiled-package.json").stat().st_size,
                "native_edges": len(source.links),
                "cross_archive_edges": len(source.legacy_links),
            }
        )
        brief["accepted_archives"].append(package.document.model_dump(mode="json"))
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
    (out / "life-reading.md").write_text("\n".join(reading) + "\n")
    write(
        out / "manifest.json",
        {
            "contract": "dense-life-corpus.1",
            "groups": reports,
            "model_calls": 0,
            "authorship": "Codex-authored fictional source; no independent provider review",
            "input_sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [
                    *sorted((ROOT / "authoring").glob("*.md")),
                    ROOT / "base-context.json",
                    ROOT / "entities.json",
                    ROOT / "weave.json",
                    ROOT / "build.py",
                ]
            },
        },
    )
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.out_dir)
