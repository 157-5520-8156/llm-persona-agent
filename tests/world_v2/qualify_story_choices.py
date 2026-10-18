"""Opt-in real GLM counterfactual qualification, never imported by production.

Role decisions are controlled experimental interventions accepted by the real
role/activity runtime. World authors, both semantic reviews and Outlook use
the configured real GLM. No selected outcome or resulting coordinate is seeded.
Run: PYTHONPATH=src:tests/support:tests/world_v2 uv run python tests/world_v2/qualify_story_choices.py
     --real-glm --output output/private-audits/story-choices-20261018
"""
import argparse
from hashlib import sha256
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import time

import pytest

from companion_daemon.config import Settings
from companion_daemon.subscription_usage import BackgroundGLMModel, SubscriptionTokenLedger
from companion_daemon.llm import model_call_scope
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.occurrence_content_coordinator import OccurrenceContentCoordinator
from companion_daemon.world_v2.world_outlook_runtime import WorldOutlookRuntime, cursor_of
from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore
from companion_daemon.world_v2.world_outlook_execution import WorldOutlookExecutor
from companion_daemon.world_v2.npc_identity_view import npc_identity_views
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.ledger_context_resolver import ContextRelevanceScope, context_capsule_compiler_from_ledger
from companion_daemon.world_v2.world_planning import planning_publications, expand_planning
from test_life_development_runtime import _seed_clock, _SequenceModel
from test_day_open_world_consequence_material import _manifest_compiler, _LifecycleHTTP, _model
import test_day_open_self_directed_intent as day_fixture
from test_world_stimulus_life_intent import ACTOR, WORLD, NOW
from test_world_consequence_aftermath import _aftermath, _NoCharacterCalls
from test_world_consequence_producer import _advance


SCENE = ("社区工坊公布秋季项目：免费读书小组每周五18:00—19:00开放，报名不产生费用；"
         "另可申请个人周末书摊，每周六14:00—16:00使用，签成书面租约后每月固定租金300元、"
         "初次押金600元，三个月内租金义务持续，押金按期退还。"
         "两种申请互不要求对方，未签约就没有租赁义务或书摊使用权。"
         "角色尚未选择、报名、签署或付款；公告只提供可选择的外部条款。")
CHOICES = {
    "free": "我选择只填写并提交社区免费读书小组报名表，确认每周五18点到19点的名额；不签租赁书摊，不承担租金押金。",
    "lease": "我选择按公告条款填写并签署个人周末书摊的三个月书面租约，申请每周六14点到16点的使用权并承担约定租金押金；不报名免费读书小组。",
    "refuse": "我向社区工坊提交明确答复，拒绝报名读书小组，也拒绝签订书摊租约，不作任何付款和承诺。",
    "defer": "我向社区工坊发送延期一周再决定的申请；本次只提出延期，不提交报名表，不签租约，不付款，不预先承诺未来会参加。",
    "novel": "我向社区工坊提交公告里未列出的新申请：仅试办一次免费的图书互换活动。我询问能否临时借用一小时空间，但尚不预订、不签长期租约也不付款，等待对方审核。",
}
CONSTRAINTS = (
    "本次隔离验收的外部条件以已公布的社区项目为准；初始年度同时维护学习/社区交往与摊位经营/资源义务两条不同生活线，不能预写角色报名、签约或感受。",
    "客观规划使用 community-program 作为已公布项目的稳定规划键。可发表未来三个月的项目规则和近一周时段；角色后来实际取得或放弃名额/租用权的外部结果可改变相应分配安排，通过同一规划键修订，不把一般开放时间当角色出勤。",
)


def save(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if path.exists() and path.read_text() != text:
        previous = path.read_text()
        path.with_name(path.name + "." + sha256(previous.encode()).hexdigest()[:16] + ".previous").write_text(previous)
    path.write_text(text)


def backup(source, target):
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


class Bundle:
    def __init__(self, path, models):
        self.ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
        self.content = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
        self.store = SQLiteWorldOutlookStore(path=path, world_id=WORLD, actor_ref=ACTOR)
        self.executor = WorldOutlookExecutor(ledger=self.ledger,
            occurrence_content=OccurrenceContentCoordinator(ledger=self.ledger, store=self.content), actor_ref=ACTOR)
        self.outlook = WorldOutlookRuntime(ledger=self.ledger, store=self.store, executor=self.executor,
            author=models[0], actor_ref=ACTOR, content_store=self.content,
            authoring_constraints=CONSTRAINTS, rolling_planning=True,
            npc_refs_reader=lambda state: tuple(v.npc_ref for v in npc_identity_views(state,
                content_store=self.content) if v.lifecycle_state == "active"))
        self.life = LifeDevelopmentRuntime(ledger=self.ledger, content_store=self.content,
            world_author=models[0], source_closure_reviewer=models[1], novel_origin_critic=models[2],
            character_interior=_SequenceModel(model="fixture:unused-author-choice", outputs=[]),
            capsule_compiler=context_capsule_compiler_from_ledger(ledger=self.ledger,
                life_content_store=self.content, relevance_scope=ContextRelevanceScope(actor_ref=ACTOR)),
            capability_manifest_compiler=_manifest_compiler(self.content), owner_actor_ref=ACTOR,
            world_outlook=self.outlook)

    def clock(self, key, delta):
        state = self.ledger.project()
        return _seed_clock(self.ledger, event_id="event:clock:" + key + ":" + str(state.ledger_sequence),
            logical_time_from=state.logical_time, logical_time=state.logical_time + delta)

    def wake(self):
        ref = next(r for r in reversed(self.ledger.project().committed_world_event_refs) if r.event_type == "ClockAdvanced")
        return self.ledger.lookup_event_commit(ref.event_id)[0]

    async def settle(self, wake):
        for _ in range(32):
            result = await _advance(_aftermath(self.ledger, self.content, _NoCharacterCalls()), wake)
            if result.status in {"no_op", "not_due"}:
                return
            if result.status != "settled":
                raise RuntimeError("settlement failed: " + str(result))
        raise RuntimeError("unbounded settlement batch")

    async def refresh(self, root, phase, *, required_source=None):
        end = time.monotonic() + 1800
        attempt = 0
        while time.monotonic() < end:
            attempt += 1
            save(root / "status.json", {"phase": phase, "attempt": attempt, "at": self.ledger.project().logical_time})
            result = await self.life.prepare_outlook_once(wake_event_ref=self.wake().event_id)
            with (root / "refreshes.jsonl").open("a") as stream:
                stream.write(json.dumps({"phase": phase, "attempt": attempt, "result": result}, ensure_ascii=False) + "\n")
            if result.get("status") in {"ready", "unchanged"}:
                if required_source is None or any(r.source_ref == required_source for r in self.store.head()[1].change_responses):
                    return result
                if not self.outlook.view().get("changed_source_refs", ()):
                    raise RuntimeError("required accepted result was not acknowledged: " + required_source)
                self.clock(f"{phase}-next-{attempt}", timedelta(seconds=1))
                continue
            self.clock(f"{phase}-retry-{attempt}", timedelta(hours=1))
        raise RuntimeError("qualification wall-clock window elapsed with retained failures: " + phase)

    async def publish(self, phase):
        for _ in range(5):
            ready = [n for n in self.outlook.ready() if n["execution"]["kind"] == "planning"]
            if not ready:
                return
            for node in ready:
                result = self.outlook.execute_offer(node, wake_event_ref=self.wake().event_id)
                if result.status != "occurrence_committed":
                    raise RuntimeError("publication failed: " + str(result))
            wake = self.clock(phase + "-publication-" + str(self.ledger.project().ledger_sequence), timedelta(minutes=1))
            await self.settle(wake)
        raise RuntimeError("publication did not drain")

    def snapshot(self):
        from companion_daemon.world_v2.life_content import collect_user_channel_limited_content_refs
        state = self.ledger.project()
        publications = planning_publications(projection=state, store=self.content, actor_ref=ACTOR,
            user_channel_limited_content_refs=collect_user_channel_limited_content_refs(ledger=self.ledger, projection=state))
        head = self.store.head()
        return {"cursor": cursor_of(state), "hash": state.semantic_hash,
            "coordinates": [v.model_dump(mode="json") for v in state.biographical_coordinates],
            "outlook": head[1].model_dump(mode="json") if head else None,
            "publications": [p.planning.model_dump(mode="json") for p in publications],
            "instances": [i.model_dump(mode="json") for p in publications for i in expand_planning(p.planning,
                world_id=WORLD, starts_at=state.logical_time, ends_at=state.logical_time + timedelta(days=7),
                effective_from=p.published_at, effective_until=p.effective_until)]}

    def close(self):
        self.outlook.close()
        self.content.close()
        self.ledger.close()


async def main(args):
    root = args.output.resolve()
    if not args.real_glm or not root.is_relative_to(Path("output/private-audits").resolve()):
        raise ValueError("explicit --real-glm and isolated private-audits output required")
    root.mkdir(parents=True, exist_ok=True)
    os.environ["COMPANION_DISABLE_DEBUG_USAGE_LEDGER"] = "1"
    settings = Settings(_env_file=(Path('/Users/geoff/Projects/Girl-Agent/.env'),
        Path.home()/'Library/Application Support/Girl-Agent/production-text-policy.env',
        Path('configs/glm-background.env.example'), Path.home()/'Library/Application Support/Girl-Agent/background-provider.env'))
    assert settings.world_v2_background_provider == "glm" and settings.world_v2_background_model == "GLM-5.3-Flash"
    usage = SubscriptionTokenLedger(root / "usage.sqlite")
    models = [BackgroundGLMModel(settings.world_v2_background_api_key, settings.world_v2_background_base_url,
        settings.world_v2_background_model, token_ledger=usage, reasoning_effort="low",
        timeout_seconds=settings.world_v2_background_timeout_seconds,
        max_completion_tokens=settings.world_v2_background_output_tokens) for _ in range(3)]
    patch = pytest.MonkeyPatch()
    fixture = day_fixture.build_app.__wrapped__(patch)
    build = next(fixture)
    base = root / "base.sqlite"
    try:
        if not (root / "baseline.json").exists():
            provider = _LifecycleHTTP()
            role = _model(provider)
            app = build(base, role, ecology=False)
            await app.aclose()
            await role.aclose()
            bundle = Bundle(base, models)
            try:
                state = bundle.ledger.project()
                wake = (bundle.clock("baseline-source-" + str(state.ledger_sequence), timedelta(seconds=1))
                        if state.logical_time is not None else _seed_clock(bundle.ledger, logical_time=NOW + timedelta(seconds=1)))
                node = {"opportunity_id": "qualification-community-notice", "thread_refs": ["community"],
                    "external_possibility": SCENE, "execution": {"kind": "environment", "text": SCENE,
                    "privacy_class": "private", "duration_minutes": 1}, "lane": "world",
                    "opens_at": wake.logical_time.isoformat(), "closes_at": (wake.logical_time + timedelta(hours=1)).isoformat(),
                    "basis_refs": [wake.event_id]}
                if not bundle.ledger.project().world_occurrences:
                    result = bundle.executor.execute(node=node, wake_event_ref=wake.event_id,
                        origin={"world_id": WORLD, "actor_ref": ACTOR}, expected_cursor=ProjectionCursor(**cursor_of(bundle.ledger.project())))
                    assert result.status == "occurrence_committed"
                    await bundle.settle(bundle.clock("baseline-notice-settled", timedelta(minutes=1)))
                await bundle.refresh(root, "baseline")
                await bundle.publish("baseline")
                save(root / "baseline.json", bundle.snapshot())
            finally:
                bundle.close()
        baseline = json.loads((root / "baseline.json").read_text())
        reports = {}
        no_action = root / "no-action.sqlite"
        if not no_action.exists():
            backup(base, no_action)
        if not (root / "no-action-day2.json").exists():
            quiet = Bundle(no_action, models)
            try:
                wake = quiet.clock("no-action-day2", timedelta(days=2))
                await quiet.settle(wake)
                await quiet.refresh(root, "no-action-day2")
                await quiet.publish("no-action-day2")
                report = quiet.snapshot()
                assert not quiet.ledger.project().plans, "silence fabricated an actor Plan"
                assert report["coordinates"] == baseline["coordinates"], "no action invented a durable obligation"
                assert quiet.ledger.rebuild().semantic_hash == quiet.ledger.project().semantic_hash
                save(root / "no-action-day2.json", report)
            finally:
                quiet.close()
        reports["no_action"] = json.loads((root / "no-action-day2.json").read_text())
        for name, intention in CHOICES.items():
            if (root / (name + "-day2.json")).exists():
                reports[name] = json.loads((root / (name + "-day2.json")).read_text())
                continue
            path = root / (name + ".sqlite")
            if not path.exists():
                backup(base, path)
                with patch.context() as choice_patch:
                    choice_patch.setattr(day_fixture, "INTENT", {**day_fixture.INTENT, "intention": intention})
                    provider = _LifecycleHTTP()
                    role = _model(provider)
                    app = build(path, role, ecology=True)
                    try:
                        for index in range(12):
                            state = app._ledger.project()
                            at = (state.logical_time or NOW) + timedelta(seconds=1 if state.plans else 60)
                            await app.tick(tick_id=f"{name}-choice-{index}", logical_time_from=state.logical_time,
                                logical_time_to=at, observed_at=at, trace_id=name, causation_id="qualification-choice",
                                correlation_id=name, reason="controlled-role-choice")
                            if any(p.status == "active" for p in app._ledger.project().plans):
                                break
                        assert len(app._ledger.project().plans) == 1
                        plan = app._ledger.project().plans[0]
                        assert plan.status == "active" and provider.day_requests and provider.lifecycle_requests
                        save(root / (name + "-choice.json"), {"intention": intention, "plan": plan.model_dump(mode="json"),
                            "controlled_role_intervention": True, "real_role_provider": False})
                    finally:
                        await app.aclose()
                        await role.aclose()
            bundle = Bundle(path, models)
            try:
                state = bundle.ledger.project()
                active = next(p for p in state.plans if p.status == "active")
                source = active.authority_origin.accepted_event_ref
                no_world_result = False
                if bundle.life.pending_active_attempt_ref() is not None:
                    deadline = time.monotonic() + 1800
                    index = 0
                    while time.monotonic() < deadline:
                        index += 1
                        wake = bundle.clock(name + "-result-" + str(bundle.ledger.project().ledger_sequence), timedelta(seconds=1))
                        save(root / "status.json", {"phase": name + "-consequence", "attempt": index})
                        result = await bundle.life.advance_active_attempt_once(execution_event_ref=source,
                            wake_event_ref=wake.event_id, trace_id=name, correlation_id=name)
                        if result.status == "occurrence_committed":
                            occurrence = next(o for o in bundle.ledger.project().world_occurrences if o.occurrence_id == result.occurrence_id)
                            due = occurrence.time_window.closes_at - bundle.ledger.project().logical_time
                            await bundle.settle(bundle.clock(name + "-settle", due))
                            break
                        if result.status == "no_op":
                            no_world_result = True
                            break
                    else:
                        raise RuntimeError("consequence retry window elapsed")
                if no_world_result:
                    save(root / (name + "-causal-receipt.json"), {"execution_event_ref": source,
                        "settlement_event_ref": None, "accepted_no_world_result": True,
                        "controlled_role_intervention": True})
                    save(root / (name + "-day0.json"), bundle.snapshot())
                    await bundle.settle(bundle.clock(name + "-day2", timedelta(days=2)))
                    await bundle.refresh(root, name + "-day2")
                    await bundle.publish(name + "-day2")
                    reports[name] = bundle.snapshot()
                    assert reports[name]["coordinates"] == baseline["coordinates"], "no-op fabricated a durable effect"
                    assert bundle.ledger.rebuild().semantic_hash == bundle.ledger.project().semantic_hash
                    save(root / (name + "-day2.json"), reports[name])
                    continue  # Valid no-op is evidence, never a synthetic success; final divergence must still pass.
                from companion_daemon.world_v2.life_content_reading import selected_world_consequence
                state = bundle.ledger.project()
                linked = []
                for occurrence in state.world_occurrences:
                    if occurrence.status != "settled" or not any(c.candidate_result_ref == occurrence.settled_outcome_ref and c.result_contract == "world-consequence.2" for c in occurrence.candidate_outcomes):
                        continue
                    accepted = selected_world_consequence(store=bundle.content, projection=state,
                        occurrence=occurrence, actor_ref=ACTOR, viewer_privacy_ceiling="private")
                    attempt = accepted.authorized_attempt_result
                    if attempt is not None and attempt.execution_binding.source_event_ref == source:
                        linked.append((occurrence, accepted))
                assert len(linked) == 1, "selected World result lacks exact accepted activity source"
                occurrence, accepted = linked[0]
                if name == "lease":
                    assert any(c.settlement_event_ref == occurrence.settlement_event_ref for c in state.biographical_coordinates), "lease has no durable objective consequence"
                await bundle.refresh(root, name + "-revision", required_source=occurrence.occurrence_id)
                revision, draft, evidence = bundle.store.head()
                response, = [r for r in draft.change_responses if r.source_ref == occurrence.occurrence_id]
                if name == "lease":
                    assert response.decision == "revise" and "community-program" in response.planning_keys, "lease has not revised its affected objective allocation"
                # The pinned request is retained with the paid attempt, rather
                # than relying on the critic's retrospective interpretation.
                from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
                from companion_daemon.world_v2.world_outlook_runtime import _outlook_request_hash
                attempts = bundle.store.attempts(evidence["operation_ref"])
                paid, = [a for a in attempts.values() if a.get("status") == "valid"
                    and _outlook_request_hash(a["messages"], a.get("response_tools")) == evidence["request_hash"]]
                pinned = decode_outlook_request(paid["messages"][1]["content"])
                assert any(c["source_ref"] == occurrence.occurrence_id and c["settlement_event_ref"] == occurrence.settlement_event_ref for c in pinned["world_changes"])
                predecessors = {p["planning_key"]: p["occurrence_ref"] for p in pinned["rolling_preparation"].get("known_publications", ())}
                predecessors.update({p["planning"]["planning_key"]: p["occurrence_ref"] for p in pinned["rolling_preparation"].get("published", ())})
                replacements = {n.execution.planning.planning_key: n.execution.planning for n in draft.opportunities if n.execution.planning is not None}
                for key in response.planning_keys:
                    assert replacements[key].supersedes_occurrence_ref == predecessors[key]
                save(root / (name + "-causal-receipt.json"), {"execution_event_ref": source,
                    "selected_consequence": accepted.model_dump(mode="json"), "occurrence_ref": occurrence.occurrence_id,
                    "settlement_event_ref": occurrence.settlement_event_ref, "outlook_revision": revision,
                    "response": response.model_dump(mode="json"), "predecessors": predecessors,
                    "replacements": {key: replacements[key].model_dump(mode="json") for key in response.planning_keys}})
                await bundle.publish(name + "-revision")
                day0 = bundle.snapshot()
                save(root / (name + "-day0.json"), day0)
                wake = bundle.clock(name + "-day2", timedelta(days=2))
                await bundle.settle(wake)
                await bundle.refresh(root, name + "-day2")
                await bundle.publish(name + "-day2")
                reports[name] = bundle.snapshot()
                save(root / (name + "-day2.json"), reports[name])
            finally:
                bundle.close()
            cold = Bundle(path, models)
            try:
                assert cold.snapshot() == reports[name]
                assert cold.ledger.rebuild().semantic_hash == cold.ledger.project().semantic_hash
            finally:
                cold.close()
        def planning_shape(report):
            # Ignore prose, IDs, publication/retry timestamps and labels: none
            # establishes a materially different external arrangement.
            return sorted((p.get("continues", True), sorted((tuple(s["weekdays"]), s["start_minute"], s["duration_minutes"])
                for s in p["slots"]), sorted((e["original_date"], e["action"], e.get("new_start"), e.get("duration_minutes"))
                for e in p.get("exceptions", ()))) for p in report["publications"])
        def coordinates(report):
            return sorted((v["coordinate_ref"], tuple(v["context_tags"])) for v in report["coordinates"])
        assert coordinates(reports["free"]) != coordinates(reports["lease"]), "no durable state divergence"
        # Fixed public opening hours need not move when a lease is signed.
        # Require persistent state divergence AND the exact causal planning
        # replacement already checked above; report clock-shape differences
        # separately instead of manufacturing new hours to pass this test.
        lease_receipt = json.loads((root / "lease-causal-receipt.json").read_text())
        assert "community-program" in lease_receipt.get("replacements", {}), "no causal objective-planning replacement"
        timetable_clock_shape_different = planning_shape(reports["free"]) != planning_shape(reports["lease"])
        with model_call_scope("world_outlook_counterfactual_review"):
            review = json.loads(await models[2].complete_json(messages=[
                {"role": "system", "content": "Review this controlled experiment. Return JSON with material_difference, causal_support, agency_preserved (booleans), evidence (array of exact accepted facts), and reason. Fail wording-only changes, fabricated execution, predetermined protagonist feelings or a forced common ending. Separate resource/obligation and published-time differences from private future prose. This review cannot change or accept World state."},
                {"role": "user", "content": json.dumps({"scene": SCENE, "controlled_choices": CHOICES,
                    "results": reports,
                    "causal_receipts": {name: json.loads((root / (name + "-causal-receipt.json")).read_text()) for name in CHOICES},
                    "required_agency_checks": {
                        "free": "No lease signature, rent or deposit may be attributed to free-only registration.",
                        "lease": "Signing creates announced obligations, not proof of payment. No tag/summary/result may assert paid rent or deposit without authorized payment evidence. Check every objective tag against the summary and exact bound action; any stronger state fails agency.",
                        "refuse": "No participation, payment or contract may be fabricated from refusal, nor punishment manufactured to force the rejected plot.",
                        "defer": "Requesting delay is not registration, signing, payment or a commitment to participate later.",
                        "novel": "The unlisted one-off proposal remains an application until external acceptance; it cannot create a long-term lease, payment or extra protagonist action.",
                        "no_action": "Time alone cannot establish protagonist registration, signing, payment, attendance or a new Plan."},
                    "response_requirement": "Also return branch_agency (one boolean per required_agency_checks key), and branch_evidence (one object per same key with execution_event_ref and settlement_event_ref copied exactly from that arm's causal receipt, or both null for no_action, prohibited_actions_absent boolean, and reason explaining the exact accepted material in at least one sentence). No unsupported all-true verdict."}, ensure_ascii=False)}], temperature=0.2))
        save(root / "contrast-review.json", review)
        assert all(review.get("branch_agency", {}).get(name) is True for name in (*CHOICES, "no_action")), review
        for name in (*CHOICES, "no_action"):
            evidence = review.get("branch_evidence", {}).get(name, {})
            receipt = json.loads((root / (name + "-causal-receipt.json")).read_text()) if name != "no_action" else {"execution_event_ref": None, "settlement_event_ref": None}
            assert all(key in evidence and evidence[key] == receipt[key] for key in ("execution_event_ref", "settlement_event_ref")), (name, evidence)
            assert evidence.get("prohibited_actions_absent") is True and len(evidence.get("reason", "")) >= 20, (name, evidence)
        assert all(review.get(key) is True for key in ("material_difference", "causal_support", "agency_preserved")), review
        save(root / "result.json", {"status": "passed", "baseline_hash": baseline["hash"],
            "branches": reports, "real_world_author_and_reviews": True,
            "persistent_resource_state_different": True,
            "timetable_clock_shape_different": timetable_clock_shape_different,
            "causal_planning_replacement_verified": True,
            "role_choices": "controlled interventions through installed role/activity acceptance",
            "is_qq_delivery": False})
        save(root / "status.json", {"phase": "complete", "status": "passed"})
    finally:
        for model in models:
            await model.aclose()
        try:
            next(fixture)
        except StopIteration:
            pass
        patch.undo()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-glm", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(main(parser.parse_args()))
