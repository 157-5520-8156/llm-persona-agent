"""Every production Plan constructor has an explicit owner and qualification path.

This structural guard detects a new writer even inside an existing module. It
is not a semantic classifier and never runs in the character's decision path.
"""

import ast
from pathlib import Path


WRITERS = {
    ("npc_ecology.py", "NpcEcology._commit_plan"): "NPC authority",
    ("world_outlook_npc.py", "CentralNpcExecutor.execute"): "NPC World authority",
    (
        "activity_plan_runtime.py",
        "ActivityPlanRuntime.plan",
    ): "observed-message application command, not an LLM author",
    (
        "activity_plan_runtime.py",
        "ActivityPlanRuntime.replace",
    ): "same source-bound application replacement",
    (
        "chat_life_intent_runtime.py",
        "derive_chat_life_plan",
    ): "protagonist inbound: test_chat_plan_response",
    (
        "world_life_intent_runtime.py",
        "make_world_life_plan",
    ): "protagonist world response: test_world_plan_continuity",
    (
        "day_open_life_intent_runtime.py",
        "_derive_day_open_life_plans",
    ): "day/completion/reconsider: test_day_open_plan_continuity",
    (
        "life_development_runtime.py",
        "LifeDevelopmentRuntime._commit_character_plan",
    ): "natural/Fate life choice: test_life_choice_plan_response",
    (
        "chat_life_intent_runtime.py",
        "ChatLifeIntentRuntime.accept",
    ): "mechanical acceptance of audited chat intention",
    (
        "world_life_intent_runtime.py",
        "WorldLifeIntentRuntime.accept",
    ): "mechanical acceptance of audited world intention",
    (
        "day_open_life_intent_runtime.py",
        "DayOpenLifeIntentRuntime.accept",
    ): "mechanical acceptance of audited daily intention",
    ("world_plan_management.py", "build_events"): "closed shared effect batch, no semantic author",
    (
        "world_plan_management.py",
        "_role_replacement",
    ): "shared derivative, no independent semantic author",
}


def test_all_plan_constructors_and_writers_have_explicit_ownership_and_qualification():
    root = Path(__file__).resolve().parents[2] / "src/companion_daemon"
    found = set()

    class Scan(ast.NodeVisitor):
        def __init__(self, path):
            self.path = path
            self.scope = []

        def visit_ClassDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            direct = name == "from_payload" and any(
                k.arg == "event_type"
                and isinstance(k.value, ast.Constant)
                and k.value.value == "ActivityPlanned"
                for k in node.keywords
            )
            helper = name in {"event", "_event"} and any(
                isinstance(v, ast.Constant) and v.value == "ActivityPlanned"
                for v in [*node.args, *[k.value for k in node.keywords]]
            )
            if name == "ActivityPlannedPayload" or direct or helper:
                found.add((self.path.name, ".".join(self.scope)))
            self.generic_visit(node)

    for path in root.rglob("*.py"):
        Scan(path).visit(ast.parse(path.read_text()))
    assert found == set(WRITERS), (
        "Unreviewed/missing Plan author: classify its owner, source proof, "
        "shared operations and public qualification; do not merely silence this guard.",
        found - set(WRITERS),
        set(WRITERS) - found,
    )
