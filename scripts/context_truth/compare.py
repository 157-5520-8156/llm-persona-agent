"""Compare Path A (seen) against Path B (ledger) for every registered slot."""

from __future__ import annotations

from typing import Any, Iterable

from .slots import SLOTS
from .types import Finding, LedgerTruth, SeenView, SlotSpec

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)) and value == 0:
        return True
    if isinstance(value, (str, bytes)) and not value:
        return True
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return True
    return False


def compare_slot(slot: SlotSpec, seen: SeenView, truth: LedgerTruth) -> Finding | None:
    seen_value = slot.extract_seen(seen)
    ledger_value = slot.extract_ledger(truth)
    detail: str | None
    if slot.compare is not None:
        detail = slot.compare(seen_value, ledger_value)
    elif slot.relation == "eq":
        if _empty(seen_value) and _empty(ledger_value):
            detail = None
        elif seen_value != ledger_value:
            detail = f"seen={seen_value!r} ledger={ledger_value!r}"
        else:
            detail = None
    elif slot.relation == "monotonic_le":
        try:
            left = 0 if seen_value is None else int(seen_value)
            right = 0 if ledger_value is None else int(ledger_value)
        except (TypeError, ValueError):
            detail = f"cannot compare monotonically: {seen_value!r} vs {ledger_value!r}"
        else:
            detail = (
                f"她看见 {left}，账本只有 {right}"
                if left > right
                else None
            )
    elif slot.relation == "approx_seconds":
        if seen_value is None and ledger_value is None:
            detail = None
        elif seen_value is None or ledger_value is None:
            detail = f"seen={seen_value!r} ledger={ledger_value!r}"
        else:
            try:
                delta = abs(int(seen_value) - int(ledger_value))
            except (TypeError, ValueError):
                detail = f"seen={seen_value!r} ledger={ledger_value!r}"
            else:
                detail = (
                    None
                    if delta <= slot.tolerance_seconds
                    else f"相差 {delta}s（容差 {slot.tolerance_seconds}s）"
                )
    elif slot.relation == "implies_positive":
        claimed = bool(seen_value)
        try:
            count = 0 if ledger_value is None else int(ledger_value)
        except (TypeError, ValueError):
            count = 1 if ledger_value else 0
        detail = (
            f"材料给出了完成/归来类事实，账本计数是 {count}"
            if claimed and count <= 0
            else None
        )
    elif slot.relation in {"subset", "head_included"}:
        detail = f"relation {slot.relation} needs a custom compare"
    else:
        detail = f"unhandled relation {slot.relation}"
    if not detail:
        return None
    kind = "mismatch"
    if slot.relation in {"eq", "monotonic_le"}:
        try:
            left = None if seen_value is None or isinstance(seen_value, bool) else int(seen_value)
            right = None if ledger_value is None or isinstance(ledger_value, bool) else int(ledger_value)
        except (TypeError, ValueError):
            left = right = None
        if left is not None and right is not None:
            if left < right:
                kind = "omission"
            elif left > right:
                kind = "invention"
        elif seen_value is False and ledger_value is True:
            kind = "omission"
        elif seen_value is True and ledger_value is False:
            kind = "invention"
    elif slot.relation == "implies_positive":
        kind = "false_claim"
    elif slot.slot_id.endswith(".companion_head") or slot.slot_id.endswith(".counterpart_head"):
        kind = "omission"
    elif slot.slot_id.endswith(".no_phantom"):
        kind = "invention"
    elif slot.known_issue == "K5":
        kind = "omission"
    elif slot.known_issue == "K6":
        kind = "omission"
    return Finding(
        severity=slot.severity,
        slot_id=slot.slot_id,
        title=slot.title,
        facets=slot.facets,
        relation=slot.relation,
        seen=seen_value,
        ledger=ledger_value,
        detail=detail,
        why_it_matters=slot.why_it_matters,
        known_issue=slot.known_issue,
        kind=kind,
    )


def run_slots(
    seen: SeenView,
    truth: LedgerTruth,
    *,
    slot_ids: Iterable[str] | None = None,
) -> list[Finding]:
    wanted = set(slot_ids) if slot_ids is not None else None
    findings = [
        finding
        for slot in SLOTS
        if wanted is None or slot.slot_id in wanted
        for finding in (compare_slot(slot, seen, truth),)
        if finding is not None
    ]
    findings.sort(
        key=lambda item: (
            SEVERITY_ORDER.index(item.severity)
            if item.severity in SEVERITY_ORDER
            else 99,
            item.slot_id,
        )
    )
    return findings
