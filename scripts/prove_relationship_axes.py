#!/usr/bin/env python3
"""Prove the relationship host chain on a production follow clone.

Never writes ``data/``, never talks to 8787 / NapCat, never restarts launchd.
Clones ``data/companion.epoch2.sqlite`` into ``output/relationship-axes/``.

Usage::

    .venv/bin/python scripts/prove_relationship_axes.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import traceback
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive

from companion_daemon.world_v2.media_opportunity_authorizer import (  # noqa: E402
    MediaOpportunityAuthorizer,
)
from companion_daemon.world_v2.present_prompt import (  # noqa: E402
    compile_slim_interior_envelope,
)
from companion_daemon.world_v2.relationship_reducers import (  # noqa: E402
    RELATIONSHIP_POLICY_DIGEST as INSTALLED_DIGEST,
)
from companion_daemon.world_v2.social_initiative import (  # noqa: E402
    SocialInitiativeContextPolicy,
    SocialInitiativePolicy,
)

WORLD_ID = drive.WORLD_ID
PRODUCTION_DB = drive.PRODUCTION_DB
OUTPUT_DIR = (REPO / "output" / "relationship-axes").resolve()
CLONE = OUTPUT_DIR / "follow.sqlite"
PRODUCTION_POST_H22_DIGEST = (
    "13bfa71dd9f8377b968714eb3d4f9a927e587832c92d2381c6ecc772071deede"
)
FRIEND_TEXT = "我们就算朋友吧"
CLOSE_FRIEND_TEXT = "我们现在算挺亲近的朋友了"
_LOG = logging.getLogger("prove_relationship_axes")


def _p3_lane(stage: str, *, adult_eligible: bool = True) -> dict[str, Any]:
    try:
        lane, maximum = MediaOpportunityAuthorizer._p3_lane_for_stage(
            stage, adult_eligible=adult_eligible
        )
        return {"ok": True, "lane": lane, "maximum": maximum, "error": None}
    except ValueError as exc:
        return {"ok": False, "lane": None, "maximum": None, "error": str(exc)}


def _variables(state: object) -> dict[str, int]:
    variables = getattr(state, "variables", None)
    names = (
        "trust_bp",
        "closeness_bp",
        "respect_bp",
        "reliability_bp",
        "mutuality_bp",
        "repair_confidence_bp",
    )
    if variables is None:
        return {name: 0 for name in names}
    return {name: int(getattr(variables, name, 0) or 0) for name in names}


def snapshot_relationship(projection: object | None) -> dict[str, Any] | None:
    if projection is None:
        return None
    states = tuple(getattr(projection, "relationship_states", ()) or ())
    if not states:
        return {
            "present": False,
            "stage": "stranger",
            "policy_digest": None,
            "entity_revision": 0,
            "variables": {},
            "commitment_refs": [],
        }
    state = states[0]
    digest = getattr(state, "policy_digest", None)
    return {
        "present": True,
        "relationship_id": getattr(state, "relationship_id", None),
        "subject_ref": getattr(state, "subject_ref", None),
        "stage": getattr(state, "stage", None),
        "entity_revision": getattr(state, "entity_revision", None),
        "policy_version": getattr(state, "policy_version", None),
        "policy_digest": digest,
        "digest_prefix": None if digest is None else str(digest)[:8],
        "installed_digest_prefix": INSTALLED_DIGEST[:8],
        "restamped_to_installed": digest == INSTALLED_DIGEST,
        "still_production_post_h22": digest == PRODUCTION_POST_H22_DIGEST,
        "variables": _variables(state),
        "commitment_refs": list(getattr(state, "commitment_refs", ()) or ()),
        "last_adjusted_at": (
            getattr(state, "last_adjusted_at", None).isoformat()
            if getattr(state, "last_adjusted_at", None) is not None
            else None
        ),
    }


def compile_initiative_profile(projection: object, logical_time: datetime) -> dict[str, Any]:
    profile = SocialInitiativeContextPolicy(policy=SocialInitiativePolicy()).compile(
        projection=projection, logical_time=logical_time
    )
    return {
        "consideration_band_seconds": list(profile.consideration_band_seconds),
        "delay_candidates_seconds": list(profile.delay_candidates_seconds),
        "candidate_weights": dict(profile.candidate_weights),
        "reason_codes": list(profile.reason_codes),
    }


def events_of_types(database: Path, after_seq: int, types: set[str]) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? AND ledger_sequence > ? ORDER BY ledger_sequence",
            (WORLD_ID, after_seq),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for seq, raw in rows:
        event = drive.parse_event(raw)
        kind = drive.event_type(event)
        if kind not in types:
            continue
        payload = event.get("_payload") or {}
        out.append(
            {
                "seq": int(seq),
                "event_type": kind,
                "logical_time": event.get("logical_time"),
                "payload": payload,
            }
        )
    return out


def flatten_codes(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"code", "reason_code", "failure_code", "error_class"} and item:
                found.append(str(item))
            found.extend(flatten_codes(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(flatten_codes(item))
    elif isinstance(value, str):
        if "uninstalled" in value or "relationship_state_policy" in value:
            found.append(value)
    return found


def hitchhike_probe() -> dict[str, Any]:
    """Does unpaired us_deltas survive compile, or vanish without a failure?"""

    base = {
        "messages": ["我记下了"],
        "felt": "心里动了一下",
        "stuck_with_me": "他认真听了",
        "wants": "把靠近留下来",
        "photo": False,
    }

    def envelope(extra: Mapping[str, object]) -> dict[str, Any]:
        compiled = compile_slim_interior_envelope({**base, **extra}, reply_only=True)
        if compiled is None:
            return {"compiled": False, "signal": None, "about_us": None, "why_us": None}
        appraisal = compiled.get("appraisal_draft") or {}
        signal = appraisal.get("relationship_signal") if isinstance(appraisal, dict) else None
        head = (compiled.get("events") or [None])[0]
        state = head.get("private_turn_state") if isinstance(head, dict) else {}
        return {
            "compiled": True,
            "signal": signal,
            "about_us": (state or {}).get("about_us") if isinstance(state, dict) else None,
            "why_us": (state or {}).get("why_us") if isinstance(state, dict) else None,
        }

    deltas = {"trust_bp": 120, "closeness_bp": 80}
    return {
        "us_deltas_only": envelope({"us_deltas": deltas}),
        "about_us_only": envelope({"about_us": "被认真听的感觉", "us_deltas": deltas}),
        "why_us_only": envelope({"why_us": "因为这一句是真的", "us_deltas": deltas}),
        "paired": envelope(
            {
                "about_us": "被认真听的感觉",
                "why_us": "因为这一句是真的",
                "us_deltas": deltas,
            }
        ),
    }


def friend_payload() -> dict[str, object]:
    return {
        "messages": [FRIEND_TEXT],
        "felt": "我想把我们算成朋友",
        "stuck_with_me": "他说到我们算什么",
        "wants": "把这一层说清楚",
        "photo": False,
        "we_are": "friend",
        "calling_it": "朋友",
        "said_as": FRIEND_TEXT,
    }


def close_friend_payload() -> dict[str, object]:
    return {
        "messages": [CLOSE_FRIEND_TEXT],
        "felt": "亲近已经过了普通朋友那一层",
        "stuck_with_me": "我想把亲近说清楚",
        "wants": "承认我们更近了",
        "photo": False,
        "we_are": "close_friend",
        "calling_it": "亲近的朋友",
        "said_as": CLOSE_FRIEND_TEXT,
    }


async def drive_commitment(
    *,
    clone: Path,
    payload: Mapping[str, object],
    user_text: str,
    label: str,
) -> dict[str, Any]:
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session = await drive.open_session(
        database=clone,
        output_dir=OUTPUT_DIR,
        inbound_payload=drive._authored_inbound_payload(dict(payload)),
        enable_media=False,
    )
    result: dict[str, Any] = {"label": label}
    try:
        inbound = await session.inbound(user_text)
        drains: list[dict[str, Any]] = []
        try:
            drains = await session.drain_loop(rounds=20, background=16)
        except Exception as exc:
            result["drain_error"] = {
                "type": type(exc).__name__,
                "message": str(exc)[:2000],
                "code": getattr(exc, "code", None),
            }
        projection = session.projection()
        now = await session.logical_time()
        relationship = snapshot_relationship(projection)
        initiative = None
        if projection is not None:
            initiative = compile_initiative_profile(projection, now)
        events = events_of_types(
            clone,
            started_seq,
            {
                "RelationshipCommitmentAccepted",
                "RelationshipSignalAccepted",
                "RelationshipSlowVariableAdjusted",
                "ActionAuthorized",
                "ActionDelivered",
                "TechnicalFailureRecorded",
                "CharacterInteriorTechnicalFailureRecorded",
                "ProposalRecorded",
                "AcceptanceRecorded",
            },
        )
        codes = flatten_codes(events) + flatten_codes(drains) + flatten_codes(
            result.get("drain_error")
        )
        result.update(
            {
                "inbound": inbound,
                "drains_tail": drains[-6:],
                "visible": session.delivery.sent,
                "relationship": relationship,
                "initiative_profile": initiative,
                "events": [
                    {
                        "seq": item["seq"],
                        "event_type": item["event_type"],
                        "logical_time": item["logical_time"],
                        "excerpt": {
                            key: (item.get("payload") or {}).get(key)
                            for key in (
                                "stage_before",
                                "stage_after",
                                "policy_digest",
                                "reason_code",
                                "failure_code",
                                "accepted_deltas",
                                "suggested_deltas",
                            )
                            if (item.get("payload") or {}).get(key) is not None
                        },
                    }
                    for item in events
                ],
                "commitment_accepted": any(
                    item["event_type"] == "RelationshipCommitmentAccepted" for item in events
                ),
                "uninstalled_codes": [
                    code
                    for code in codes
                    if "relationship_state_policy_uninstalled" in code
                    or "uninstalled" in code
                ],
                "cost": drive.cost_report(clone, since_id=usage_from),
            }
        )
        return result
    except Exception as exc:
        result["error"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:2000],
            "traceback": traceback.format_exc()[-4000:],
        }
        result["cost"] = drive.cost_report(clone, since_id=usage_from)
        return result
    finally:
        await session.close()


async def drive_initiative_draw(*, clone: Path) -> dict[str, Any]:
    """Tick just past idle so the scheduler records a delay draw without a model turn."""

    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session = await drive.open_session(
        database=clone, output_dir=OUTPUT_DIR, enable_media=False
    )
    try:
        before = snapshot_relationship(session.projection())
        now = await session.logical_time()
        profile_before = None
        if session.projection() is not None:
            profile_before = compile_initiative_profile(session.projection(), now)
        # 31 minutes: past spontaneous_idle (30m), still below friend 2h and stranger 6h.
        tick = await session.tick_to(
            now + timedelta(minutes=31),
            reason="initiative-draw",
            run_life=False,
        )
        drains: list[dict[str, Any]] = []
        drain_error = None
        try:
            drains = await session.drain_loop(rounds=12, background=16)
        except Exception as exc:
            drain_error = {
                "type": type(exc).__name__,
                "message": str(exc)[:2000],
                "code": getattr(exc, "code", None),
            }
        draws = events_of_types(clone, started_seq, {"RandomDrawRecorded"})
        initiative_draws = []
        for item in draws:
            payload = item.get("payload") or {}
            catalog = str(payload.get("catalog_version") or "")
            if catalog != "social-initiative-delay.1":
                continue
            selected = str(payload.get("selected_candidate_ref") or "")
            delay = None
            if selected.startswith("delay:"):
                try:
                    delay = int(selected.removeprefix("delay:"))
                except ValueError:
                    delay = None
            candidates = []
            for ref in payload.get("candidate_refs") or ():
                if isinstance(ref, str) and ref.startswith("delay:"):
                    try:
                        candidates.append(int(ref.removeprefix("delay:")))
                    except ValueError:
                        continue
            initiative_draws.append(
                {
                    "seq": item["seq"],
                    "delay_seconds": delay,
                    "delay_hours": None if delay is None else round(delay / 3600, 3),
                    "candidate_seconds": candidates,
                    "selected_candidate_ref": selected,
                    "attempt_id": payload.get("attempt_id"),
                }
            )
        after = snapshot_relationship(session.projection())
        return {
            "relationship_before": before,
            "relationship_after": after,
            "compiled_profile": profile_before,
            "tick": tick,
            "drains_tail": drains[-4:],
            "drain_error": drain_error,
            "initiative_draws": initiative_draws,
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "error": {
                "type": type(exc).__name__,
                "message": str(exc)[:2000],
                "traceback": traceback.format_exc()[-4000:],
            },
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


def grade_cells(report: dict[str, Any]) -> dict[str, Any]:
    friend = report.get("friend_drive") or {}
    close = report.get("close_friend_drive") or {}
    draw = report.get("initiative_draw") or {}
    friend_rel = friend.get("relationship") or {}
    close_rel = close.get("relationship") or {}
    uninstalled = list(friend.get("uninstalled_codes") or []) + list(
        close.get("uninstalled_codes") or []
    )
    draws = draw.get("initiative_draws") or []
    sampled = draws[-1] if draws else None
    sampled_delay = None if sampled is None else sampled.get("delay_seconds")
    sampled_candidates = [] if sampled is None else list(sampled.get("candidate_seconds") or [])
    friend_band = {7200, 10800, 14400}
    stranger_band = {21600, 25200, 28800}

    cells = {
        "A1_compiler_no_uninstalled": {
            "pass": not uninstalled
            and "relationship_state_policy_uninstalled"
            not in json.dumps(friend, ensure_ascii=False)
            and friend.get("error") is None,
            "evidence": {
                "uninstalled_codes": uninstalled,
                "friend_drain_error": friend.get("drain_error"),
                "friend_error": friend.get("error"),
            },
        },
        "A2_stage_became_friend": {
            "pass": friend_rel.get("stage") == "friend"
            and bool(friend.get("commitment_accepted")),
            "evidence": {
                "stage": friend_rel.get("stage"),
                "commitment_accepted": friend.get("commitment_accepted"),
                "commitment_refs": friend_rel.get("commitment_refs"),
                "entity_revision": friend_rel.get("entity_revision"),
            },
        },
        "A3_digest_restamped": {
            "pass": bool(friend_rel.get("restamped_to_installed"))
            and not friend_rel.get("still_production_post_h22"),
            "evidence": {
                "before_expected": PRODUCTION_POST_H22_DIGEST[:8],
                "after_prefix": friend_rel.get("digest_prefix"),
                "installed_prefix": INSTALLED_DIGEST[:8],
                "restamped_to_installed": friend_rel.get("restamped_to_installed"),
            },
        },
        "A4_initiative_draw_is_friend_band": {
            "pass": sampled_delay is not None
            and 7200 <= int(sampled_delay) <= 14400
            and set(sampled_candidates).issubset(friend_band)
            and not set(sampled_candidates).intersection(stranger_band),
            "evidence": {
                "sampled": sampled,
                "compiled_profile": draw.get("compiled_profile"),
                "note": (
                    "Scheduler must actually draw from 2–4h candidates; "
                    "compiled parameters alone do not pass this cell."
                ),
            },
        },
        "A5_close_friend_opens_p3_gate": {
            "pass": close_rel.get("stage") == "close_friend"
            and (report.get("p3_after_close_friend") or {}).get("ok") is True
            and (report.get("p3_after_friend") or {}).get("ok") is False,
            "evidence": {
                "close_friend_stage": close_rel.get("stage"),
                "p3_after_friend": report.get("p3_after_friend"),
                "p3_after_close_friend": report.get("p3_after_close_friend"),
                "commitment_accepted": close.get("commitment_accepted"),
            },
        },
    }
    for cell in cells.values():
        cell["verdict"] = "pass" if cell["pass"] else "FAIL"
    return cells


def write_markdown(report: dict[str, Any]) -> str:
    cells = report["cells"]
    lines = [
        "# 关系宿主链路验收（生产跟进克隆）",
        "",
        "只读打开生产账本并备份到 `output/relationship-axes/follow.sqlite`。",
        "未写 `data/`，未碰 8787 / NapCat / launchd。",
        "",
        "## A. 逐格结果",
        "",
        "| 格 | 判定 | 要点 |",
        "|---|---|---|",
    ]
    labels = {
        "A1_compiler_no_uninstalled": "compiler 不再抛 uninstalled",
        "A2_stage_became_friend": "阶段 stranger → friend 落账",
        "A3_digest_restamped": "13bfa71d 自动 restamp",
        "A4_initiative_draw_is_friend_band": "主动联系抽到 2–4 小时",
        "A5_close_friend_opens_p3_gate": "close_friend 打开 P3 关系门",
    }
    for key, title in labels.items():
        cell = cells[key]
        evidence = json.dumps(cell["evidence"], ensure_ascii=False)[:180]
        lines.append(f"| {title} | **{cell['verdict']}** | `{evidence}` |")
    lines += [
        "",
        f"- 克隆：`{report.get('clone')}`",
        f"- 安装摘要：`{INSTALLED_DIGEST}`",
        f"- 总花费 CNY：{report.get('cost_cny')}",
        "",
        "## B. hitchhike 成对探测（无宿主）",
        "",
        "```json",
        json.dumps(report.get("hitchhike_probe"), ensure_ascii=False, indent=2),
        "```",
        "",
        "完整 JSON：`output/relationship-axes/REPORT.json`。",
        "",
    ]
    return "\n".join(lines)


async def async_main() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    drive.clone_ledger(PRODUCTION_DB, CLONE)
    hitchhike = hitchhike_probe()
    usage_from = drive.current_usage_id(CLONE)
    friend = await drive_commitment(
        clone=CLONE,
        payload=friend_payload(),
        user_text="我们现在算什么",
        label="we_are=friend",
    )
    p3_friend = _p3_lane(str((friend.get("relationship") or {}).get("stage") or "stranger"))
    draw = await drive_initiative_draw(clone=CLONE)
    close = await drive_commitment(
        clone=CLONE,
        payload=close_friend_payload(),
        user_text="我们是不是更近了",
        label="we_are=close_friend",
    )
    p3_close = _p3_lane(str((close.get("relationship") or {}).get("stage") or "friend"))
    cost = drive.cost_report(CLONE, since_id=usage_from)
    report = {
        "clone": str(CLONE),
        "source": str(PRODUCTION_DB),
        "installed_digest": INSTALLED_DIGEST,
        "production_post_h22_digest": PRODUCTION_POST_H22_DIGEST,
        "hitchhike_probe": hitchhike,
        "friend_drive": friend,
        "initiative_draw": draw,
        "close_friend_drive": close,
        "p3_after_friend": p3_friend,
        "p3_after_close_friend": p3_close,
        "cost": cost,
        "cost_cny": cost.get("cost_cny"),
        "finished_at": datetime.now(UTC).isoformat(),
    }
    report["cells"] = grade_cells(report)
    (OUTPUT_DIR / "REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    markdown = write_markdown(report)
    (OUTPUT_DIR / "CELLS.md").write_text(markdown, encoding="utf-8")
    return report


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    os.chdir(REPO)
    report = asyncio.run(async_main())
    print(json.dumps({"cells": report["cells"], "cost_cny": report.get("cost_cny")}, ensure_ascii=False, indent=2))
    print(f"cells: {OUTPUT_DIR / 'CELLS.md'}")
    print(f"full report: {OUTPUT_DIR / 'REPORT.md'}")


if __name__ == "__main__":
    main()
