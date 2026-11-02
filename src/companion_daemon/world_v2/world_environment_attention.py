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
    "自然语言字段请用平实、直接、接地气的现代日常白话，严禁写文绉绉的文艺散文腔（如灯影、潮气、书页折角），"
    "也绝不要写插座检修、台灯接触不良、保洁拖地、食堂白汽这类毫无戏剧张力的静物或家务白噪音！"
    "采样到的地点坐标只是外部事件借以发生的空间舞台，而非要求描写房间静物；"
    "请完全根据当前客观时间、人生阶段、既有故事线与人际背景，自然推演会真正影响她处境的外部机缘、人际互动、现实压力或意外插曲。"
    "2-4个分支结果之间必须拉开鲜明的现实走向差距（如顺利推进并带来积极转机 vs 遭遇现实阻碍、人际分歧或突发状况并留下后续连锁影响）。"
    "只创造本次权限允许的外部事实；主角如何反应、向谁倾诉或如何选择，完全由角色自己决定。"
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
