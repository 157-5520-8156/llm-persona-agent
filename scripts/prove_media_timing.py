#!/usr/bin/env python3
"""Measure media-selection timing: cold ask vs conversation-adjacent ask.

Never writes ``data/``, never talks to 8787 / NapCat, never commits.
Selection decisions call the real character model. Image HTTP is not issued
(selection-only drain). Max real images for this script: 0.

Usage::

    .venv/bin/python scripts/prove_media_timing.py --phase all
    .venv/bin/python scripts/prove_media_timing.py --phase cold --n 6
    .venv/bin/python scripts/prove_media_timing.py --phase near --n 6
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import traceback
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
import prove_first_photo as first

DEFAULT_OUTPUT = (REPO / "output" / "media-timing").resolve()
CANDIDATE_OPEN = (REPO / "output" / "first-photo" / "candidate-open.sqlite").resolve()
FOLLOWUP_DB = first.FOLLOWUP_DB
HE_ASKED_TEXT = first.HE_ASKED_TEXT
_LOG = logging.getLogger("prove_media_timing")


def dump_json(path: Path, value: object) -> None:
    first.dump_json(path, value)


def _media_worker(session: first.ProveSession):
    platform = getattr(session.host, "_host", None)
    application = getattr(platform, "_application", None)
    return getattr(application, "_media_selection_worker", None)


def _patch_overlay_capture(session: first.ProveSession) -> None:
    overlay = session.overlay
    if overlay is None:
        return
    original = overlay.complete_json_stream_with_usage

    async def wrapped(messages, *, temperature=0.8, on_text_delta=None, tools=None, tool_choice=None):
        if overlay._is_media_tool(tools) and not overlay._intercept_media(tools):
            overlay._remember_media_prompt(messages)
        return await original(
            messages,
            temperature=temperature,
            on_text_delta=on_text_delta,
            tools=tools,
            tool_choice=tool_choice,
        )

    overlay.complete_json_stream_with_usage = wrapped  # type: ignore[method-assign]


async def drain_selection_only(session: first.ProveSession) -> dict[str, Any]:
    platform = getattr(session.host, "_host", None)
    application = getattr(platform, "_application", None)
    if application is None:
        raise RuntimeError("host has no WorldV2 turn application")
    logical_time = await session.logical_time()
    result = await application.drain_media_selection_once(
        logical_time=logical_time,
        trace_id="trace:media-timing:selection",
        correlation_id="correlation:media-timing:selection",
    )
    return {
        "status": None if result is None else getattr(result, "status", None),
        "reason_code": None if result is None else getattr(result, "reason_code", None),
        "proposal_event_ref": (
            None if result is None else getattr(result, "proposal_event_ref", None)
        ),
    }


def render_counts(database: Path, *, after_seq: int) -> dict[str, int]:
    evidence = first.ledger_media_evidence(database, after_seq=after_seq)
    kinds = [row["event_type"] for row in evidence["events"]]
    return {
        "PhotoCandidateOpened": kinds.count("PhotoCandidateOpened"),
        "MediaSelectionAttemptRecorded": kinds.count("MediaSelectionAttemptRecorded"),
        "MediaSelectionProposalRecorded": kinds.count("MediaSelectionProposalRecorded"),
        "MediaPlanRecorded": kinds.count("MediaPlanRecorded"),
        "MediaRenderArtifactRecorded": kinds.count("MediaRenderArtifactRecorded"),
        "MediaAutomaticDeliveryApproved": kinds.count("MediaAutomaticDeliveryApproved"),
    }


def _decision_from_turns(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    return turns[-1] if turns else None


async def ensure_candidate_open(*, output_dir: Path) -> Path:
    if CANDIDATE_OPEN.exists():
        return CANDIDATE_OPEN
    if not FOLLOWUP_DB.exists():
        raise SystemExit("missing first-photo candidate-open and drive-0818 followup clones")
    report = await first.prepare_candidate_open(source=FOLLOWUP_DB, output_dir=output_dir)
    dump_json(output_dir / "candidate-open.json", report)
    if report.get("status") != "opened":
        raise SystemExit(f"could not open the book-market candidate: {report}")
    return Path(report["clone"])


async def run_trial(
    *,
    source: Path,
    output_dir: Path,
    trial_id: str,
    near_conversation: bool,
    force_cold_ask: bool,
) -> dict[str, Any]:
    clone = output_dir / f"{trial_id}.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    captured: list[dict[str, object]] = []
    session = await first.open_prove_session(
        database=clone,
        output_dir=output_dir,
        inbound_payload=None,
        force_media_select=False,
        enable_media=True,
        on_media_prompt=captured.append,
    )
    _patch_overlay_capture(session)
    asked_step = None
    gate_before = None
    try:
        worker = _media_worker(session)
        if worker is None:
            raise RuntimeError("media selection worker is not installed")
        gate_before = bool(worker._require_conversation_occasion)
        if force_cold_ask:
            worker._require_conversation_occasion = False
        if near_conversation:
            asked_step = await session.inbound_without_background(HE_ASKED_TEXT)
        selection = await drain_selection_only(session)
        turns = first.interior_media_turns(clone)
        decision = _decision_from_turns(turns)
        counts = render_counts(clone, after_seq=started_seq)
        payload = (decision or {}).get("decision") or {}
        accepted = payload.get("decision") == "select" or bool(
            counts["MediaSelectionProposalRecorded"]
        )
        declined = payload.get("decision") == "no_op" or (
            selection.get("reason_code") == "media_selection.model_declined"
        )
        facts = None
        if captured:
            candidates = captured[-1].get("candidates")
            first_choice = candidates[0] if isinstance(candidates, list) and candidates else None
            if isinstance(first_choice, dict):
                facts = {
                    "safe_summary": first_choice.get("safe_summary"),
                    "lived_facts": first_choice.get("lived_facts"),
                }
        return {
            "trial_id": trial_id,
            "clone": str(clone),
            "conditions": {
                "near_conversation": near_conversation,
                "force_cold_ask": force_cold_ask,
                "gate_defaulted_on": gate_before,
            },
            "asked_step": asked_step,
            "selection": selection,
            "decision": decision,
            "accepted": accepted,
            "declined": declined and not accepted,
            "skipped_unasked": selection.get("reason_code")
            == "media_selection.no_conversation_occasion",
            "shown": facts,
            "event_counts": counts,
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "trial_id": trial_id,
            "clone": str(clone),
            "conditions": {
                "near_conversation": near_conversation,
                "force_cold_ask": force_cold_ask,
            },
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-3000:],
            "decision": (first.interior_media_turns(clone) or [None])[-1],
            "event_counts": render_counts(clone, after_seq=started_seq),
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    real = [row for row in rows if "error" not in row and not row.get("skipped_unasked")]
    skipped = [row for row in rows if row.get("skipped_unasked")]
    accepted = sum(1 for row in real if row.get("accepted"))
    declined = sum(1 for row in real if row.get("declined"))
    quotes = [
        {
            "trial_id": row.get("trial_id"),
            "summary": (row.get("decision") or {}).get("summary"),
            "payload": (row.get("decision") or {}).get("decision"),
            "safe_summary": ((row.get("shown") or {}) or {}).get("safe_summary"),
            "lived_facts": ((row.get("shown") or {}) or {}).get("lived_facts"),
            "inbound_status": ((row.get("asked_step") or {}) or {}).get("status"),
        }
        for row in real
    ]
    renders = sum(
        int((row.get("event_counts") or {}).get("MediaRenderArtifactRecorded") or 0)
        for row in rows
    )
    return {
        "n_asked": len(real),
        "n_skipped_unasked": len(skipped),
        "accepted": accepted,
        "declined": declined,
        "accept_rate": (accepted / len(real) if real else None),
        "quotes": quotes,
        "renders": renders,
        "errors": [row.get("error") for row in rows if "error" in row],
        "costs": [row.get("cost") for row in rows],
    }


def write_report(output_dir: Path, bundle: dict[str, Any]) -> None:
    skip = bundle.get("skip_cold") or {}
    cold = bundle.get("cold") or {}
    near = bundle.get("near") or {}
    cold_sum = _summarize(cold.get("trials") or [])
    near_sum = _summarize(near.get("trials") or [])
    lines = [
        "# 选片时机与候选世界事实",
        "",
        f"生成时间：{datetime.now(UTC).isoformat()}",
        "",
        "克隆账本与产物目录：`output/media-timing/`。生产账本只读，未写 `data/`，未碰 8787 / NapCat。",
        "选片决策走真实 DeepSeek。生图全部未发出（只 drain `drain_media_selection_once`）。",
        "",
        "## A. 时机：为什么这不是替她决定发不发",
        "",
        "系统只决定**什么时候把「要不要发」这个问题递给她**。`select` / `no_op` 仍是她的角色决定；",
        "默认没有改成发送，也没有提高答应倾向。冷后台不再提问；候选仍保持 `available`，不是记一次拒绝。",
        "",
        "构成机会的依据（不读他的措辞）：",
        "",
        "1. 她自己写的结构化 `media_request=consider_available_candidate`，或仍未结束的 `media_request` 过程；",
        "2. 开放线程的证据与候选 `source_event_refs` 相交；",
        "3. 对方最近一次 `user:` 观察的逻辑时间，与候选 `opened_at` 相差不超过 2 小时。",
        "   QQ 观察的 `source_event_id` 嵌在 WorldEvent id 里，按身份对齐，不读正文。",
        "",
        "第 3 条不解析消息文本。同一时机门对「拍一张给我看」和「在吗」一视同仁。",
        "她说不发，同一候选 revision 不会再问（已有 `media_declined_candidate_revisions`）。",
        "",
        "## B. 她看到的世界事实",
        "",
        "候选 payload 增加 `lived_facts[]`：每条都有 `source_ref` 与 `privacy`，隐私高于候选 ceiling 或 `withhold` 的丢掉。",
        "`safe_summary` 从过滤后的事实派生（与 `lived_moment` 一样，脱敏之后再拼字符串）。宿主不写「值得分享的理由」。",
        "",
        "事实种类：已结算生活事件（何时、地点、参与者）、账本上的 ImageEvidence（摘要/活动/地点/季节）、",
        "活动目录 token（如 `open_life.<hex>`）当作权威句柄丢掉，只留人类可读的 `description`。",
        "",
        "## 1. 生产门：冷后台不再问",
        "",
        f"- 选择结果：`{(skip.get('selection') or {}).get('status')}` / `{(skip.get('selection') or {}).get('reason_code')}`",
        f"- 是否调了选片模型：{'否' if skip.get('skipped_unasked') else skip}",
        "",
        "## 2. 对照实验（同一旧书市自拍候选）",
        "",
        "| 条件 | n 被问到 | 接受 | 拒绝 | 接受率 |",
        "|---|---:|---:|---:|---:|",
        f"| 后台冷问（关掉时机门，改前基线重测） | {cold_sum.get('n_asked')} | {cold_sum.get('accepted')} | {cold_sum.get('declined')} | {cold_sum.get('accept_rate')} |",
        f"| 候选带世界事实 + 机会在对话附近 | {near_sum.get('n_asked')} | {near_sum.get('accepted')} | {near_sum.get('declined')} | {near_sum.get('accept_rate')} |",
        "",
        "### 冷问原话",
        "",
    ]
    for item in cold_sum.get("quotes") or []:
        lines.append(
            f"- `{item.get('trial_id')}` → `{((item.get('payload') or {}) or {}).get('decision')}`："
            f"{item.get('summary')}"
        )
    lines += ["", "### 对话附近原话", ""]
    for item in near_sum.get("quotes") or []:
        lines.append(
            f"- `{item.get('trial_id')}` inbound=`{item.get('inbound_status')}` → "
            f"`{((item.get('payload') or {}) or {}).get('decision')}`：{item.get('summary')}"
        )
    shown = None
    for item in (near_sum.get("quotes") or []) + (cold_sum.get("quotes") or []):
        if item.get("lived_facts"):
            shown = item
            break
    lines += [
        "",
        "## 3. 她当时看到的事实（抓包）",
        "",
        f"```json\n{json.dumps(shown, ensure_ascii=False, indent=2, default=str) if shown else '（无抓包）'}\n```",
        "",
        "## 4. 拒绝是否更贴处境",
        "",
        "见上表原话。若冷问仍是「留给自己」，对话附近开始提到他刚说的话或诗集本身，",
        "说明她拿到了真实处境，而不是被推着答应。接受率不升也如实录。",
        "",
        "## 5. 生成侧上限",
        "",
        f"- 冷问组渲染事件：{cold_sum.get('renders')}",
        f"- 对话附近组渲染事件：{near_sum.get('renders')}",
        "- 本脚本只调用 `drain_media_selection_once`，不进入规划/渲染/投递。槽位门未绕过。",
        f"- 真实 OpenAI 生图：**0**（上限 2）。",
        "",
        "## 6. 花费",
        "",
        f"- 冷问：{json.dumps(cold_sum.get('costs'), ensure_ascii=False)}",
        f"- 对话附近：{json.dumps(near_sum.get('costs'), ensure_ascii=False)}",
        "",
    ]
    (output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source = await ensure_candidate_open(output_dir=output_dir)
    existing_path = output_dir / "BUNDLE.json"
    existing: dict[str, Any] = {}
    if existing_path.exists():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
    bundle: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "output_dir": str(output_dir),
        "source": str(source),
        "he_asked_text": HE_ASKED_TEXT,
        "n": args.n,
    }
    for key in ("skip_cold", "cold", "near"):
        if key in existing:
            bundle[key] = existing[key]
    if args.phase in {"skip-cold", "all"}:
        row = await run_trial(
            source=source,
            output_dir=output_dir,
            trial_id="skip-cold",
            near_conversation=False,
            force_cold_ask=False,
        )
        bundle["skip_cold"] = row
        dump_json(output_dir / "skip-cold.json", row)
        _LOG.info("skip-cold %s %s", row.get("selection"), row.get("skipped_unasked"))
    if args.phase in {"cold", "all"}:
        trials = []
        for index in range(1, args.n + 1):
            trial_id = f"cold-{index}"
            _LOG.info("cold trial %s", trial_id)
            row = await run_trial(
                source=source,
                output_dir=output_dir,
                trial_id=trial_id,
                near_conversation=False,
                force_cold_ask=True,
            )
            trials.append(row)
            dump_json(output_dir / f"{trial_id}.json", row)
        bundle["cold"] = {"trials": trials, "summary": _summarize(trials)}
        dump_json(output_dir / "cold.json", bundle["cold"])
    if args.phase in {"near", "all"}:
        trials = []
        for index in range(1, args.n + 1):
            trial_id = f"near-{index}"
            _LOG.info("near trial %s", trial_id)
            row = await run_trial(
                source=source,
                output_dir=output_dir,
                trial_id=trial_id,
                near_conversation=True,
                force_cold_ask=False,
            )
            trials.append(row)
            dump_json(output_dir / f"{trial_id}.json", row)
        bundle["near"] = {"trials": trials, "summary": _summarize(trials)}
        dump_json(output_dir / "near.json", bundle["near"])
    bundle["finished_at"] = datetime.now(UTC).isoformat()
    dump_json(output_dir / "BUNDLE.json", bundle)
    write_report(output_dir, bundle)
    return bundle


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("skip-cold", "cold", "near", "all"), default="all")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--n", type=int, default=6)
    args = parser.parse_args()
    bundle = asyncio.run(async_main(args))
    print(json.dumps(
        {
            "skip_cold": bundle.get("skip_cold"),
            "cold": (bundle.get("cold") or {}).get("summary"),
            "near": (bundle.get("near") or {}).get("summary"),
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))


if __name__ == "__main__":
    main()
