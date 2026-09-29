"""Bound redundant narrative history in a model view, never in World storage.

Current state, counterpart reports, facts, capabilities, recalled material and
all action/effect coordinates remain intact. This is an explicit experimental
presentation policy, not a source of facts or a character behavior policy.
"""
from copy import deepcopy

from ..shared_string_view import unpack_shared_strings

CONTRACT = "inbound-narrative-window.1"


def bounded_inbound_context(packet):
    result = deepcopy(packet)
    snapshot = result.get("inner_life_snapshot")
    if not isinstance(snapshot, dict):
        return result
    materials = snapshot.get("materials")
    if isinstance(materials, dict) and materials.get("contract") == "shared-string-view.1":
        materials = unpack_shared_strings(materials)
        snapshot["materials"] = materials
    if not isinstance(materials, dict):
        return result
    omitted = {}
    dialogue = materials.get("recent_dialogue")
    if isinstance(dialogue, dict) and isinstance(dialogue.get("stable_turns"), list):
        rows = dialogue["stable_turns"]
        own = [i for i, row in enumerate(rows) if isinstance(row, dict) and row.get("speaker") == "companion"]
        keep = set(own[-2:])
        own_indices = set(own)
        dialogue["stable_turns"] = [row for i, row in enumerate(rows) if i not in own_indices or i in keep]
        omitted["older_companion_utterances"] = len(own) - len(keep)
    appraisals = materials.get("appraisals")
    if isinstance(appraisals, dict) and isinstance(appraisals.get("stable_rows"), list):
        columns = appraisals.get("columns", [])
        rows = appraisals["stable_rows"]
        if "since" in columns:
            from datetime import datetime
            index = columns.index("since")
            try:
                ordered = sorted(range(len(rows)), key=lambda i: datetime.fromisoformat(rows[i][index]))
            except (TypeError, ValueError, IndexError):
                ordered = list(range(len(rows)))
            keep = set(ordered[-4:])
            appraisals["stable_rows"] = [row for i, row in enumerate(rows) if i in keep]
            omitted["older_appraisal_narratives"] = len(rows) - len(keep)
    # Retain every current affect component and its semantic state, while the
    # original snapshot/receipt keeps the verbose proof reference tables.
    affect = materials.get("affect")
    if isinstance(affect, dict):
        entries = list(affect.get("stable_entries", []))
        if isinstance(affect.get("volatile_last_entry"), dict):
            entries.append(affect["volatile_last_entry"])
        for entry in entries:
            if isinstance(entry, dict):
                for component in entry.get("components", []):
                    if isinstance(component, dict):
                        component.pop("appraisal_refs", None)
    has_correction = "role_result_correction" in result
    correction = result.pop("role_result_correction", None)
    result["context_window"] = {
        "contract": CONTRACT,
        "scope": "model_presentation_only_not_deletion_or_complete_history",
        "omitted": omitted,
        "instruction": "这是本轮展示窗口，不是全部历史。旧发言和旧解释仍可检索；没展示不证明没发生。当前情绪、关系、事实和未完成事项仍保留，如何回应由你决定。",
    }
    if has_correction:
        result["role_result_correction"] = correction
    return result
