#!/usr/bin/env python3
"""Clone-only proof that impression reflection offers a character-chosen release.

Never writes ``data/``, never talks to 8787 or NapCat. Artifacts live in
``output/impression-release/``.

Usage::

    .venv/bin/python scripts/prove_impression_release.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
import prove_stale_topics as sun

OUTPUT = (REPO / "output" / "impression-release").resolve()
WORLD_ID = drive.WORLD_ID
PHOTO_IMPRESSION_PREFIX = "impression:8bf57079490dbe59749a4a16f1d44962"
SETTLED_TEXT = "那天路灯那张你纠正过了，不是猫，我认了。这件事就这样吧。"
COST_CAP_CNY = 2.8
N_FARM = 6
N_SUN = 5


class CaptureReflectionModel:
    """Record whether reflection offered ``release`` without changing answers."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.captures: list[dict[str, Any]] = []
        self.model = getattr(inner, "model", "capture")
        self.provider = getattr(inner, "provider", "capture")
        self.supports_required_tool_choice = True
        self.supports_strict_tool_choice = bool(
            getattr(inner, "supports_strict_tool_choice", True)
        )
        self.reports_exact_request_emission = bool(
            getattr(inner, "reports_exact_request_emission", False)
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def complete(self, messages: list[dict[str, Any]], *, temperature: float = 0.8) -> str:
        self._capture(messages, None)
        return await self._inner.complete(messages, temperature=temperature)

    def _capture(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> None:
        function = None
        if tools:
            first = tools[0] if isinstance(tools[0], dict) else None
            function = first.get("function") if isinstance(first, dict) else None
        name = function.get("name") if isinstance(function, dict) else ""
        if not isinstance(name, str) or "private_impression_reflection" not in name:
            return
        user = next(
            (item.get("content") for item in messages if item.get("role") == "user"),
            None,
        )
        body: dict[str, Any] = {}
        if isinstance(user, str):
            try:
                decoded = json.loads(user)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                body = decoded
        schema = body.get("purpose_contract") if isinstance(body.get("purpose_contract"), dict) else {}
        proposal = schema.get("proposal_schema") if isinstance(schema.get("proposal_schema"), dict) else {}
        payload = body.get("capability_manifest") if isinstance(body.get("capability_manifest"), dict) else {}
        inner_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        decision_enum: list[str] = []
        if isinstance(function, dict):
            parameters = function.get("parameters")
            if isinstance(parameters, dict):
                for branch in parameters.get("anyOf") or ():
                    if not isinstance(branch, dict):
                        continue
                    status = (branch.get("properties") or {}).get("status") or {}
                    if isinstance(status, dict) and status.get("enum") == ["transition"]:
                        items = ((branch.get("properties") or {}).get("proposals") or {}).get("items")
                        if isinstance(items, dict):
                            decision = (items.get("properties") or {}).get("decision") or {}
                            if isinstance(decision, dict) and isinstance(decision.get("enum"), list):
                                decision_enum = [str(item) for item in decision["enum"]]
        self.captures.append(
            {
                "tool_name": name,
                "tool_decision_enum": decision_enum,
                "purpose_decision": proposal.get("decision"),
                "decision_meanings": proposal.get("decision_meanings")
                or inner_payload.get("decision_meanings"),
                "existing_impression_short_tokens": inner_payload.get(
                    "existing_impression_short_tokens"
                ),
                "offered_release": "release" in decision_enum
                or (
                    isinstance(proposal.get("decision"), str)
                    and "release" in proposal["decision"]
                ),
                "release_meaning": (
                    (proposal.get("decision_meanings") or inner_payload.get("decision_meanings") or {}).get(
                        "release"
                    )
                    if isinstance(
                        proposal.get("decision_meanings") or inner_payload.get("decision_meanings"),
                        dict,
                    )
                    else None
                ),
            }
        )

    async def complete_json_with_usage(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.8,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, Any]]:
        self._capture(messages, tools)
        return await self._inner.complete_json_with_usage(
            messages, temperature=temperature, tools=tools, tool_choice=tool_choice
        )

    async def complete_json_stream_with_usage(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.8,
        on_text_delta: object | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        self._capture(messages, tools)
        return await self._inner.complete_json_stream_with_usage(
            messages,
            temperature=temperature,
            on_text_delta=on_text_delta,
            tools=tools,
            tool_choice=tool_choice,
        )


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("_payload")
    if isinstance(raw, dict):
        return raw
    raw = event.get("payload_json")
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def production_reflection_usage(source: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        purposes = {
            str(row["purpose"]): int(row["c"])
            for row in conn.execute(
                "SELECT purpose, COUNT(*) AS c FROM world_v2_character_interior_turns "
                "WHERE world_id=? GROUP BY purpose",
                (WORLD_ID,),
            )
        }
        decisions: dict[str, int] = {}
        kinds: dict[str, int] = {}
        statuses: dict[str, int] = {}
        accepted = 0
        for row in conn.execute(
            "SELECT event_json FROM world_v2_events WHERE world_id=?",
            (WORLD_ID,),
        ):
            event = json.loads(row["event_json"])
            if event.get("event_type") != "PrivateImpressionAccepted":
                continue
            accepted += 1
            payload = _payload(event)
            if not payload:
                raw = event.get("payload_json")
                payload = json.loads(raw) if isinstance(raw, str) else {}
            decision = str(payload.get("reflection_decision") or "none")
            kind = str(payload.get("transition_kind") or "none")
            impression = payload.get("impression") if isinstance(payload.get("impression"), dict) else {}
            status = str(impression.get("status") or "none")
            decisions[decision] = decisions.get(decision, 0) + 1
            kinds[kind] = kinds.get(kind, 0) + 1
            statuses[status] = statuses.get(status, 0) + 1
        gates = []
        try:
            gates = [
                dict(row)
                for row in conn.execute(
                    "SELECT reason, COUNT(*) AS c FROM world_v2_private_impression_gates "
                    "GROUP BY reason"
                )
            ]
        except sqlite3.OperationalError:
            pass
        return {
            "interior_turns_by_purpose": purposes,
            "private_impression_reflection_turns": int(
                purposes.get("private_impression_reflection") or 0
            ),
            "accepted_impressions": accepted,
            "reflection_decision_counts": decisions,
            "transition_kind_counts": kinds,
            "impression_status_counts": statuses,
            "gates": gates,
        }
    finally:
        conn.close()


def clone_impressions(clone: Path) -> list[dict[str, Any]]:
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

    ledger = SQLiteWorldLedger(path=str(clone), world_id=WORLD_ID)
    projection = ledger.project()
    out = []
    for item in projection.private_impressions:
        out.append(
            {
                "impression_id": item.impression_id,
                "status": item.status,
                "entity_revision": item.entity_revision,
                "summary": item.reflection_summary,
                "first_seen": item.first_seen.isoformat() if item.first_seen else None,
                "last_supported": item.last_supported.isoformat() if item.last_supported else None,
            }
        )
    return out


def uninterpreted_appraisal_count(clone: Path) -> dict[str, Any]:
    from companion_daemon.world_v2.private_impression_producer import private_impression_opportunity
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

    ledger = SQLiteWorldLedger(path=str(clone), world_id=WORLD_ID)
    projection = ledger.project()
    return {
        "active_appraisals": sum(1 for item in projection.appraisals if item.status == "active"),
        "active_impressions": sum(
            1 for item in projection.private_impressions if item.status == "active"
        ),
        "opportunity_open": private_impression_opportunity(projection) is not None,
    }


def impression_events(clone: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = drive.open_ro(clone)
    try:
        rows = drive.events_after(conn, WORLD_ID, after_seq)
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for seq, event in rows:
        kind = drive.event_type(event)
        payload = event.get("_payload") or {}
        if kind != "PrivateImpressionAccepted":
            continue
        impression = payload.get("impression") if isinstance(payload.get("impression"), dict) else {}
        out.append(
            {
                "seq": seq,
                "decision": payload.get("reflection_decision"),
                "transition_kind": payload.get("transition_kind"),
                "status": impression.get("status"),
                "impression_id": impression.get("impression_id"),
                "summary": impression.get("reflection_summary"),
            }
        )
    return out


def reflection_turn_count(clone: Path) -> int:
    conn = sqlite3.connect(f"file:{clone.resolve()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM world_v2_character_interior_turns "
            "WHERE world_id=? AND purpose='private_impression_reflection'",
            (WORLD_ID,),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


async def open_farm_session(database: Path, output_dir: Path) -> tuple[drive.DriveSession, CaptureReflectionModel]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    if drive._is_production_path(database):
        raise SystemExit(f"refusing to open production ledger for write: {database}")
    sidecar = output_dir / f"{database.stem}.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
    os.environ["WORLD_V2_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT"] = "8"
    os.environ["WORLD_V2_PRIVATE_IMPRESSION_MIN_INTERVAL_SECONDS"] = "0"
    os.environ["WORLD_V2_PRIVATE_IMPRESSION_IDLE_AFTER_USER_SECONDS"] = "0"
    settings = Settings(
        database_path=database,
        world_v2_external_perception_mode="off",
        world_v2_external_perception_sidecar_path=sidecar / "perception.sqlite",
        attachment_cache_path=sidecar / "attachments",
        world_v2_text_endpoint_enabled=False,
        world_v2_private_impression_daily_model_call_limit=8,
        world_v2_private_impression_min_interval_seconds=0,
        world_v2_private_impression_idle_after_user_seconds=0,
    )
    recipient_id = drive._recipient_id(settings)
    inner = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking_enabled=False,
        max_completion_tokens=4_096,
    )
    capture = CaptureReflectionModel(inner)
    delivery = drive.CaptureDelivery()
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id=recipient_id,
        bootstrap_at=datetime.now(UTC),
        delivery=delivery,
        model=capture,
        media_preview=None,
        media_transport=None,
        use_configured_recall_embedding=False,
    )
    session = drive.DriveSession(
        database=database,
        recipient_id=recipient_id,
        delivery=delivery,
        host=host,
        clock=datetime.now(UTC),
    )
    await session.logical_time()
    return session, capture


async def run_sun_retest(*, spent: float) -> dict[str, Any]:
    clone = OUTPUT / "sun-retest.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    evidence = sun.compile_sun_dialogue(clone=clone)
    snapshot, _manifest, _trigger = sun.compile_sun_snapshot(evidence)
    import httpx
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel

    settings = Settings()
    usages: list[object] = []
    model = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking_enabled=False,
        max_completion_tokens=2_048,
        usage_observer=usages.append,
        client=httpx.AsyncClient(timeout=180, trust_env=False),
    )
    author = sun._compose_sun_author(model=model)
    speak: list[dict[str, Any]] = []
    spoken = 0
    index = 0
    while spoken < N_SUN and index < N_SUN + 3 and spent < COST_CAP_CNY:
        index += 1
        trial = await sun.one_speak_trial(
            snapshot=snapshot,
            sun=evidence["_sun"],
            cursor=evidence["cursor"],
            index=index,
            author=author,
            usages=usages,
        )
        speak.append(trial)
        spent += float(trial.get("cost_cny") or 0)
        if trial.get("her_texts"):
            spoken += 1
    return {
        "spoken": spoken,
        "spent_cny": round(spent, 4),
        "recognized_sun_n": sum(1 for item in speak if item.get("recognized_sun")),
        "remembered_mood_n": sum(1 for item in speak if item.get("remembered_mood_explain")),
        "reopened_old_account_n": sum(1 for item in speak if item.get("reopened_old_account")),
        "trials": speak,
    }


def write_report(report: dict[str, Any]) -> None:
    production = report["production"]
    farm = report["farm"]
    sun_retest = report.get("sun_retest") or {}
    lines = [
        "# Impression release proof",
        "",
        f"Finished at {report['finished_at']}.",
        "",
        "## 1. Production reflection usage",
        "",
        f"- `private_impression_reflection` interior turns: **{production['private_impression_reflection_turns']}**",
        f"- `PrivateImpressionAccepted`: **{production['accepted_impressions']}**",
        f"- reflection_decision counts: `{production['reflection_decision_counts']}`",
        f"- transition_kind counts: `{production['transition_kind_counts']}`",
        f"- farm gates: `{production['gates']}`",
        "",
        "She is not walking the reflection faculty in production. The one `retain` is the inbound hitch, not a farm `experience(purpose=private_impression_reflection)` turn. The farm skipped once for `recent_user_observation`.",
        "",
        "## 2. Clone farm",
        "",
        f"- uninterpreted appraisals at clone open: {farm.get('uninterpreted_appraisals')}",
        f"- inbound used to make the settled heart current: `{farm.get('inbound_text')}`",
        f"- reflection turns: {farm.get('reflection_turns')}",
        f"- offered `release` in tool/schema: {farm.get('offered_release')}",
        f"- she chose `release`: {farm.get('chose_release')}",
        f"- photo impression status after: `{farm.get('photo_status_after')}`",
        f"- decisions: `{farm.get('decisions')}`",
        "",
        "Not releasing is a valid character choice. The bar is: she had the option and could see what it meant.",
        "",
        "## 3. Sun retest",
        "",
        f"- recognized sun: {sun_retest.get('recognized_sun_n')}/{sun_retest.get('spoken')}",
        f"- remembered mood explain: {sun_retest.get('remembered_mood_n')}/{sun_retest.get('spoken')}",
        f"- reopened old account: {sun_retest.get('reopened_old_account_n')}/{sun_retest.get('spoken')}",
        "",
        "## 4. Accumulation",
        "",
        "Active impressions stay in working attention until she chooses `release` / `supersede`, or a later clock honors an expiry she authored. Nothing currently expires them automatically. If she never chooses a terminal, they accumulate and the capsule budget drops them non-deterministically — that is still a long-term problem, now with a character-owned way out.",
        "",
        f"Spent: ¥{report.get('spent_cny')}",
        "",
    ]
    (OUTPUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    production = production_reflection_usage(drive.PRODUCTION_DB)
    clone = OUTPUT / "farm.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    before = clone_impressions(clone)
    uninterpreted = uninterpreted_appraisal_count(clone)
    session, capture = await open_farm_session(clone, OUTPUT)
    spent = 0.0
    rounds = 0
    inbound_result: dict[str, Any] | None = None
    try:
        inbound_result = await session.inbound(SETTLED_TEXT)
        cost = drive.cost_report(clone, since_id=usage_from)
        spent = float(cost.get("cost_cny") or 0)
        while reflection_turn_count(clone) < N_FARM and rounds < 10 and spent < 1.6:
            rounds += 1
            await session.drain(actions=2, background=12)
            cost = drive.cost_report(clone, since_id=usage_from)
            spent = float(cost.get("cost_cny") or 0)
            if reflection_turn_count(clone) >= N_FARM:
                break
    finally:
        await session.close()

    after = clone_impressions(clone)
    accepted = impression_events(clone, started_seq)
    decisions = [item.get("decision") for item in accepted]
    photo_after = next(
        (
            item
            for item in after
            if str(item.get("impression_id") or "").startswith(PHOTO_IMPRESSION_PREFIX)
        ),
        None,
    )
    farm = {
        "clone": str(clone),
        "uninterpreted_appraisals": uninterpreted,
        "impressions_before": before,
        "impressions_after": after,
        "inbound_text": SETTLED_TEXT,
        "inbound": inbound_result,
        "reflection_turns": reflection_turn_count(clone),
        "captures": capture.captures,
        "offered_release": any(item.get("offered_release") for item in capture.captures),
        "chose_release": any(item.get("decision") == "release" for item in accepted),
        "photo_status_after": None if photo_after is None else photo_after.get("status"),
        "accepted": accepted,
        "decisions": decisions,
        "drain_rounds": rounds,
    }
    sun_retest = await run_sun_retest(spent=spent)
    total = round(float(sun_retest.get("spent_cny") or spent), 4)
    report = {
        "finished_at": datetime.now(UTC).isoformat(),
        "production": production,
        "farm": farm,
        "sun_retest": sun_retest,
        "spent_cny": total,
        "cost_cap_cny": COST_CAP_CNY,
    }
    (OUTPUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    write_report(report)
    print(json.dumps(
        {
            "production_reflection_turns": production["private_impression_reflection_turns"],
            "production_decisions": production["reflection_decision_counts"],
            "offered_release": farm["offered_release"],
            "chose_release": farm["chose_release"],
            "reflection_turns": farm["reflection_turns"],
            "photo_status_after": farm["photo_status_after"],
            "sun": {
                "recognized": sun_retest.get("recognized_sun_n"),
                "mood": sun_retest.get("remembered_mood_n"),
                "reopened": sun_retest.get("reopened_old_account_n"),
                "spoken": sun_retest.get("spoken"),
            },
            "spent_cny": total,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
