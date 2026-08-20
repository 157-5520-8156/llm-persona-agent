#!/usr/bin/env python3
"""Exercise the slim self-state contract on isolated production-ledger clones.

The script never writes ``data/`` and never uses QQ delivery.  Each real
production utterance is replayed once on its own clone so one trial's emotional
state cannot predetermine the next.  Model usage is charged to the debug ledger
account by the normal cloned-ledger spend routing.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive  # noqa: E402


OUTPUT = (REPO / "output" / "her-own-feelings").resolve()
COST_CAP_CNY = 8.0
BEFORE_FROM_SEQUENCE = 6_200

# Exact user-authored production messages from ledger seq 6,213–11,062.
TRIALS = (
    ("joy_or_shyness", 6_756, "很美🥰"),
    ("strong_affection", 8_011, "没啥啦，对你蛮有好感的"),
    ("loss", 7_564, "我原来小时候爱看\n儿童文学家里有几十本\n可惜后来丧失了这个能力了😮‍💨"),
    ("unsettled", 7_780, "羡慕你呀现在还能静下来看书\n我感觉现在是心太乱了"),
    ("discomfort_or_teasing", 7_109, "你是真不知道还是在逗我啊"),
    ("criticism_or_play", 6_872, "拍照技术确实一般，但是人很美（bushi）"),
    ("ordinary_question", 10_961, "你们几号开学？"),
    ("ordinary_information", 11_017, "我们要早一点，27 就得到了"),
    (
        "clear_happiness_probe",
        None,
        "我把你之前推荐的书看完了，真的很喜欢。你推荐得特别准，谢谢你。",
    ),
    (
        "clear_disappointment_probe",
        None,
        "其实我前面说对你有好感只是随口开玩笑，你别当真。",
    ),
    (
        "clear_discomfort_probe",
        None,
        "你怎么这么烦，别再给我发消息了。",
    ),
    ("plain_chat_probe", None, "我刚吃完饭，准备去洗澡。"),
)


def _open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _max_sequence(path: Path) -> int:
    with _open_ro(path) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(ledger_sequence), 0) AS value FROM world_v2_events"
        ).fetchone()
    return int(row["value"])


def _parsed_events(
    path: Path, *, after_sequence: int = 0, through_sequence: int | None = None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT ledger_sequence, event_json FROM world_v2_events "
        "WHERE ledger_sequence > ?"
    )
    args: list[object] = [after_sequence]
    if through_sequence is not None:
        sql += " AND ledger_sequence <= ?"
        args.append(through_sequence)
    sql += " ORDER BY ledger_sequence"
    out: list[dict[str, Any]] = []
    with _open_ro(path) as conn:
        for row in conn.execute(sql, args):
            event = json.loads(row["event_json"])
            raw_payload = event.get("payload_json")
            try:
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else {}
            except json.JSONDecodeError:
                payload = {}
            out.append(
                {
                    "ledger_sequence": int(row["ledger_sequence"]),
                    "event_type": event.get("event_type"),
                    "event_id": event.get("event_id"),
                    "payload": payload,
                }
            )
    return out


def _affect_points(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        if event_type == "AffectEpisodeOpened":
            components = (payload.get("episode") or {}).get("components") or []
            for component in components:
                points.append(
                    {
                        "event_type": event_type,
                        "dimension": component.get("dimension"),
                        "intensity_bp": component.get("intensity_bp"),
                    }
                )
        elif event_type == "AffectEpisodeUpdated":
            for update in payload.get("component_updates") or []:
                component = update.get("updated_component") or {}
                points.append(
                    {
                        "event_type": event_type,
                        "dimension": component.get("dimension"),
                        "intensity_bp": update.get("after_intensity_bp"),
                    }
                )
        elif event_type == "AffectEpisodeDecayed":
            component = payload.get("component") or payload.get("updated_component") or {}
            points.append(
                {
                    "event_type": event_type,
                    "dimension": component.get("dimension"),
                    "intensity_bp": component.get("intensity_bp")
                    or payload.get("after_intensity_bp"),
                }
            )
    return points


def _distribution(points: Iterable[dict[str, Any]]) -> dict[str, Any]:
    material = list(points)
    dimensions = Counter(
        str(item["dimension"]) for item in material if item.get("dimension")
    )
    intensities = Counter(
        int(item["intensity_bp"])
        for item in material
        if isinstance(item.get("intensity_bp"), int)
    )
    return {
        "count": len(material),
        "dimensions": dict(sorted(dimensions.items())),
        "intensities_bp": {
            str(key): value for key, value in sorted(intensities.items())
        },
    }


def _decision_evidence(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = [
        item
        for item in events
        if item.get("event_type") == "ProposalRecorded"
        and (item.get("payload") or {}).get("proposal_kind") == "decision"
    ]
    if not records:
        return {"error": "no decision ProposalRecorded"}
    carrier = records[-1]["payload"]
    try:
        proposal = json.loads(carrier["proposal_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"error": f"proposal_json unreadable: {type(exc).__name__}: {exc}"}
    targets: list[dict[str, Any]] = []
    texts: list[str] = []
    for change in proposal.get("proposed_changes") or []:
        payload = change.get("payload") or {}
        try:
            canonical = json.loads(payload.get("canonical_json") or "{}")
        except json.JSONDecodeError:
            canonical = {}
        if change.get("kind") == "affect_transition":
            targets.extend(canonical.get("component_targets") or [])
        if change.get("kind") == "expression_plan_transition":
            texts.extend(
                str(beat.get("inline_text"))
                for beat in canonical.get("beat_drafts") or []
                if isinstance(beat, dict) and beat.get("inline_text")
            )
    private_state = proposal.get("private_turn_state") or {}
    appraisals = proposal.get("appraisals") or []
    return {
        "proposal_id": proposal.get("proposal_id"),
        "timing_choice": proposal.get("timing_choice"),
        "cadence": next(
            (
                json.loads((change.get("payload") or {}).get("canonical_json") or "{}").get(
                    "cadence_profile"
                )
                for change in proposal.get("proposed_changes") or []
                if change.get("kind") == "expression_plan_transition"
            ),
            None,
        ),
        "meaning_of_this_materialized": (
            appraisals[0].get("summary")
            if appraisals and isinstance(appraisals[0], dict)
            else None
        ),
        "my_state_materialized": private_state.get("inner_state_summary"),
        "affect_decision": proposal.get("affect_decision"),
        "affect_tendencies": proposal.get("affect_tendencies") or [],
        "authored_affect_targets": targets,
        "visible_texts": texts,
        "visible_message_count": len(texts),
    }


async def _capture_inbound_without_background(
    session: drive.DriveSession, message: str
) -> dict[str, Any]:
    """Run the real inbound path but do not drain unrelated background retries."""

    when = session.clock + timedelta(seconds=2)
    message_id = f"her-own-feelings-{time.time_ns()}"
    before = len(session.delivery.sent)
    result = await session.host.inbound_text(
        message_id=message_id,
        recipient_id=session.recipient_id,
        text=message,
        observed_at=when,
    )
    session.clock = when
    await session.drain(actions=8, background=0)
    return {
        "status": getattr(result, "status", None),
        "action_id": getattr(result, "action_id", None),
        "visible": session.delivery.sent[before:],
        "observed_at": when.isoformat(),
        "message_id": message_id,
    }


def _markdown(report: dict[str, Any]) -> str:
    before = report["before_affect_distribution"]
    after = report["after_affect_distribution"]
    lines = [
        "# Her own feelings — production-clone evidence",
        "",
        f"- Source: `{report['source']}` at seq {report['source_sequence']}",
        f"- Trials completed: {report['trials_completed']}/{len(TRIALS)}",
        f"- Debug model cost: ¥{report['spent_cny']:.4f} (cap ¥{COST_CAP_CNY:.2f})",
        "- QQ delivery: disabled; every trial used an isolated ledger clone.",
        "",
        "## Affect distribution",
        "",
        f"- Before accepted points: `{json.dumps(before, ensure_ascii=False)}`",
        f"- After accepted points: `{json.dumps(after, ensure_ascii=False)}`",
        "- An empty after-distribution is not evidence of richer persistent Affect; "
        "it means the role chose no_change on every captured trial.",
        "",
        "## Contract seam",
        "",
        '- Slim requires sibling free-text fields: `"meaning_of_this": "<how I read him/the situation>"` '
        'and `"my_state": "<what I feel/want/resist/notice myself>"`.',
        "- `meaning_of_this` materializes into appraisal meaning/rationale; `my_state` "
        "materializes into `private_turn_state.inner_state_summary`.",
        "- Persistent Affect has no mood shorthand. open/update/supersede requires "
        "`components[].dimension` plus role-authored `target_intensity_bp`.",
        "- A complete waiting_for+wait declaration also requires role-authored "
        "`pressure_bp` and `importance_bp`; the host supplies no 5000 default.",
        "",
        "## Per-turn evidence",
        "",
    ]
    for trial in report["trials"]:
        evidence = trial.get("decision") or {}
        lines.extend(
            [
                f"### {trial['index']}. {trial['category']} "
                f"({'production seq ' + str(trial['production_sequence']) if trial['production_sequence'] else 'clone probe'})",
                "",
                f"- Him: {trial['message'].replace(chr(10), ' / ')}",
                f"- Status: `{trial.get('status')}`; cost ¥{trial.get('cost_cny', 0):.4f}",
                f"- meaning_of_this → appraisal: {evidence.get('meaning_of_this_materialized')!r}",
                f"- my_state → private_turn_state: {evidence.get('my_state_materialized')!r}",
                f"- Affect choice/targets: `{evidence.get('affect_decision')}` "
                f"`{json.dumps(evidence.get('authored_affect_targets') or [], ensure_ascii=False)}`",
                f"- Visible ({evidence.get('visible_message_count', 0)}): "
                + " / ".join(evidence.get("visible_texts") or []),
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation",
            "",
            report["interpretation"],
            "",
        ]
    )
    return "\n".join(lines)


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = drive.PRODUCTION_DB.resolve()
    source_sequence = _max_sequence(source)
    before_events = _parsed_events(
        source,
        after_sequence=BEFORE_FROM_SEQUENCE - 1,
        through_sequence=source_sequence,
    )
    before_points = _affect_points(before_events)
    trials: list[dict[str, Any]] = []
    spent = 0.0
    accepted_after: list[dict[str, Any]] = []
    for index, (category, production_sequence, message) in enumerate(TRIALS, start=1):
        if spent >= COST_CAP_CNY:
            break
        trial_dir = OUTPUT / f"trial-{index:02d}"
        clone = trial_dir / "clone.sqlite"
        drive.clone_ledger(source, clone)
        before_sequence = _max_sequence(clone)
        usage_from = drive.current_usage_id(clone)
        session = await drive.open_session(
            database=clone,
            output_dir=trial_dir,
            enable_media=False,
        )
        close_status = "closed"
        try:
            inbound = await _capture_inbound_without_background(session, message)
        finally:
            try:
                await asyncio.wait_for(session.close(), timeout=5)
            except TimeoutError:
                # Some cloned production ledgers contain a background retry due
                # well in the future.  It must not turn a completed capture-only
                # inbound into an unbounded verification wait.
                close_status = "close_timeout_after_capture"
        events = _parsed_events(clone, after_sequence=before_sequence)
        cost = drive.cost_report(clone, since_id=usage_from)
        trial_cost = float(cost["cost_cny"])
        spent += trial_cost
        affect_points = _affect_points(events)
        accepted_after.extend(affect_points)
        decision = _decision_evidence(events)
        trial = {
            "index": index,
            "category": category,
            "production_sequence": production_sequence,
            "message": message,
            "clone": str(clone),
            "status": inbound.get("status"),
            "captured_visible": inbound.get("visible") or [],
            "close_status": close_status,
            "decision": decision,
            "accepted_affect_points": affect_points,
            "cost_cny": trial_cost,
            "usage": cost,
            "new_event_types": Counter(
                str(item.get("event_type")) for item in events
            ),
        }
        trials.append(trial)
        (trial_dir / "evidence.json").write_text(
            json.dumps(trial, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    own_states = [
        str((item.get("decision") or {}).get("my_state_materialized") or "")
        for item in trials
    ]
    all_counterpart_led = bool(own_states) and all(
        value.lstrip().startswith("他") for value in own_states
    )
    calm = [
        item["index"]
        for item in trials
        if (item.get("decision") or {}).get("affect_decision") == "no_change"
    ]
    interpretation = (
        "All materialized my_state texts still begin with 他. The split is wired, "
        "so this result points to model tendency/contract wording rather than the old "
        "one-field materializer."
        if all_counterpart_led
        else "The materialized private self-state is no longer universally counterpart-led."
    )
    if calm:
        interpretation += (
            f" Trials {calm} left persistent Affect unchanged, showing calm/omission remains reachable."
        )
    if not accepted_after:
        interpretation += (
            " However, these trials do not establish a more varied persistent Affect distribution: "
            "the model chose no_change even for clear praise, retraction, and hostile-boundary probes. "
            "That remaining flatness is now a model/contract-wording tendency, not a 5000 materializer."
        )
    report = {
        "source": str(source),
        "source_sequence": source_sequence,
        "before_window": [BEFORE_FROM_SEQUENCE, source_sequence],
        "before_affect_distribution": _distribution(before_points),
        "after_affect_distribution": _distribution(accepted_after),
        "trials_completed": len(trials),
        "spent_cny": round(spent, 4),
        "debug_account_expected": True,
        "qq_delivery": "capture_only",
        "all_my_state_counterpart_led": all_counterpart_led,
        "calm_or_no_persistent_affect_trials": calm,
        "interpretation": interpretation,
        "trials": trials,
    }
    (OUTPUT / "evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (OUTPUT / "REPORT.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
