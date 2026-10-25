"""Sample an offered environment to attend to, never a character act or fact.

This advisory request material is pinned with the ordinary author request.
Reconstruction uses only offered coordinates and the original cursor, with no
mutable RNG, topic classifier, invented event, or additional model call.
"""

from copy import deepcopy
import hashlib
import json


GUIDANCE = (
    "This ordinary world-generation opening samples the external setting in "
    "environment_attention.coordinate. Author a candidate development originating "
    "in that setting, or choose no_op. This selects a part of the world to simulate, "
    "never the protagonist's location, action, interest or response. It is not "
    "evidence that anyone is present or has perceived anything there. Recent "
    "consequences constrain consistency; they are not a request to continue the "
    "last incident or connect this setting to it. An unrelated part of the world "
    "can change independently. Author concrete observable particulars rather "
    "than saying that particulars exist. Preserve all source, timing and privacy "
    "rules; no new permission is granted by this attention coordinate."
)

INDEPENDENT_SCENE_GUIDANCE = (
    "This is an independent ordinary opening. Choose whether to continue a sourced "
    "development or author an unrelated one, and choose its scale, tone and lasting consequences. "
    "Avoid repeating generic facility maintenance, janitorial mopping or canteen background noise "
    "already overrepresented in recent_life_texture; vary tone across ordinary moments, unexpected "
    "snags, minor setbacks, awkward or lively social encounters, and substantive developments. "
    "The character's current feelings, plans and the user's personal reports "
    "do not select what independently happens in this setting. Nothing here "
    "chooses what she does, notices, likes, or says. Ordinary, positive and adverse events "
    "are all welcome; when proposing multiple outcomes, include genuinely distinct branches "
    "(e.g. smooth progress vs a frustrating obstacle or twist)."
)

ORDINARY_SCENE_LANGUAGE = (
    "自然语言字段请用中文。由你决定本次是日常插曲、意外小挫折、人际碰撞还是会产生持续后果的发展，"
    "以及承接既有故事还是发生独立的新事。避免反复写保洁拖地、食堂白汽、挪桌椅等雷同背景白噪音；"
    "分支结果之间应有真实差异（如顺利进行 vs 遇到阻碍、扑空或突发状况）。"
    "只创造本次权限允许的外部事实；主角是否参与、注意、委屈、吐槽或求助，仍由角色自己决定。"
    "协议键、枚举和来源坐标保持原样。"
)


def independent_scene_context(context):
    """External generator audience: retain World records, not a chat agenda.

    The original capsule, role's memory and all ledger facts remain intact.
    Attempt-result authors still receive their exact execution context through
    the original path. This narrower view cannot grant an absent source.
    """
    if not isinstance(context, dict) or not isinstance(context.get("slices"), dict):
        return deepcopy(context)
    return {**deepcopy(context),
            "slices": {key: deepcopy(value) for key, value in context["slices"].items() if key == "world_life"},
            "audience": "independent-world-scene.1",
            "scope": "external_world_history_not_character_or_counterpart_agenda"}


def environment_attention(context: dict) -> dict | None:
    manifest = context.get("capability_manifest", {})
    # Exact attempt-result requests must stay on that original causal scope.
    if any(manifest.get(key) is not None for key in (
        "active_attempt_consequence", "completed_activity_consequence", "event_detail_capability",
    )):
        return None
    if context.get("occasion_mode") == "disturbance":
        return None
    timing = context.get("timing_coordinates", {})
    offered = {
        (row["location_ref"], row["capability_ref"])
        for row in manifest.get("location_capabilities", [])
    }
    candidates = [
        row for row in timing.get("location_capability_coordinates", [])
        if (row.get("location_ref"), row.get("capability_ref")) in offered
        and (row.get("maximum_now_duration_minutes") or 0) >= 5
    ]
    if not candidates:
        return None
    seed = json.dumps({
        "cursor": manifest.get("pinned_cursor"),
        "actor": manifest.get("owner_actor_ref"),
        "clock": timing.get("pinned_logical_time"),
        "anchors": sorted(manifest.get("anchor_refs", [])),
    }, sort_keys=True, separators=(",", ":"))
    selected = min(candidates, key=lambda row: hashlib.sha256(
        (seed + "\n" + row["capability_ref"]).encode()
    ).digest())
    return {
        "contract": "world-environment-attention.1",
        "scope": "candidate_environment_only_not_presence_or_execution",
        "selection": "deterministic_hash_sample_of_currently_available_capabilities",
        "seed_sha256": hashlib.sha256(seed.encode()).hexdigest(),
        "coordinate": deepcopy(selected),
    }
