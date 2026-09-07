"""Synthetic transport fixture for accelerated host journeys, never production.

The fixture exercises installed contracts and scheduling. It deliberately does
not create a rich life, varied character choices, or human-quality evidence.
"""

from __future__ import annotations

import hashlib
import json

from companion_daemon.llm import FakeCompanionModel


class LongitudinalFixtureModel(FakeCompanionModel):
    model = "longitudinal-fixture.1"
    provider = "offline-fixture"

    async def complete(self, messages, *, temperature: float = 0.8) -> str:
        payload = {}
        for message in reversed(messages):
            try:
                candidate = json.loads(message["content"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if "inner_turn" in payload:
            self.calls.append(messages)
            return json.dumps(self._role_result(payload), ensure_ascii=False)
        authority = payload.get("authority", {})
        if authority.get("role") == "one_npc_actor":
            from .npc_ecology import NpcSocialVariables

            self.calls.append(messages)
            return json.dumps(
                {
                    "decision": "no_op",
                    "npc_ref": authority["selected_npc_ref"],
                    "impulse_summary": "Offline fixture leaves this NPC unchanged.",
                    "inner_state_summary": "Synthetic fixture; no independent life is generated.",
                    "source_refs": sorted(set(authority["input_event_refs"])),
                    "relationship_to_protagonist": NpcSocialVariables().model_dump(mode="json"),
                    "current_goal_summaries": [],
                    "proposal": None,
                }
            )
        output_contract = payload.get("output_contract", {})
        if isinstance(output_contract, dict) and "no_op" in output_contract:
            self.calls.append(messages)
            return '{"decision":"no_op"}'
        joined = "\n".join(message["content"] for message in messages)
        if "You maintain the long-term user-fact memory" in joined:
            self.calls.append(messages)
            if "observations" in payload:
                return json.dumps(
                    {
                        "decisions": [
                            {"observation_id": item["observation_id"], "result": {"retain": False}}
                            for item in payload["observations"]
                        ]
                    }
                )
            return '{"retain":false}'
        if "NpcWorldDecision" in joined:
            self.calls.append(messages)
            return '{"decision":"no_op","outcomes":[]}'
        if "meaning_of_this" in joined and "my_state" in joined:
            self.calls.append(messages)
            return json.dumps(
                {
                    "messages": ["离线夹具：收到这条消息了。"],
                    "meaning_of_this": "This synthetic fixture received the current input.",
                    "my_state": "Offline fixture; no claim of real character quality.",
                },
                ensure_ascii=False,
            )
        return await super().complete(messages, temperature=temperature)

    async def complete_json_stream_with_usage(
        self,
        messages,
        *,
        temperature: float = 0.8,
        on_text_delta=None,
    ):
        raw = await self.complete(messages, temperature=temperature)
        if on_text_delta is not None:
            on_text_delta(raw)
        usage = {
            "usage_contract": "model-usage.1",
            "route_class": "chat",
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "token_provenance": "offline_estimated",
            "transport": "offline_fixture",
            "provider": self.provider,
            "provider_usage_ref": "usage:longitudinal-fixture:stream",
        }
        usage["provider_usage_hash"] = hashlib.sha256(
            json.dumps(usage, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return raw, usage

    @staticmethod
    def _role_result(payload: dict) -> dict:
        purpose = payload["inner_turn"]["purpose"]
        result = {
            "status": "no_change",
            "summary": "Synthetic fixture leaves this unchanged.",
            "attended_source_refs": [],
            "decision": None,
            "recall_query": None,
            "proposals": [],
        }
        if purpose == "world_stimulus_appraisal":
            result["proposals"] = [
                {
                    "proposal_type": "world_stimulus_appraisal_result",
                    "decision": "no_change",
                    "brief_rationale": "Offline fixture.",
                    "behavior_tendency": "unchanged",
                    "stance": "unchanged",
                    "display_strategy": "withhold",
                    "confidence": 3000,
                    "meaning_candidates": None,
                    "attribution": None,
                    "severity": None,
                    "expiry": None,
                    "affect_transition": None,
                    "relationship_signal": None,
                }
            ]
            return result
        if "no_change" in payload["wire_contract"]["allowed_statuses"]:
            return result
        if "silent" in payload["wire_contract"]["allowed_statuses"]:
            result["status"] = "silent"
            return result
        capability = payload["capability_manifest"]
        refs = list(capability.get("source_refs", ()))
        if not refs:
            refs = list(payload["inner_turn"].get("subject_source_refs", ()))
        if not refs:
            raise ValueError("fixture cannot invent a missing decision source")
        if purpose == "proactive_contact":
            decision = {
                "timing_choice": "silent",
                "beats": [],
                "stance": "unchanged",
                "brief_rationale": "Offline fixture chooses no external expression.",
                "impulse_summary": "Synthetic fixture.",
                "confidence": 3000,
                "world_claims": [],
            }
        elif purpose in {"fact_memory_retention", "experience_memory_retention"}:
            decision = {"retain": False}
        elif purpose == "life_development_choice":
            decision = {"completion": {"decision": "no_op"}}
        else:
            decision = {"decision": "no_op"}
        result.update(status="decision", decision={"source_refs": refs[:1], "payload": decision})
        return result
