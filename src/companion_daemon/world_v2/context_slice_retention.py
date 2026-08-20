"""Guard against silent loss of resolved Context Capsule slice content."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from .context_capsule import CapsuleSlice, ContextCapsule, TruncationEntry, TruncationReason
from .fact_predicate_stability import fact_predicate_is_stable

# A resolved slice with this many items should not compile to far fewer without
# an auditable truncation reason covering the gap.
_RETENTION_RATIO_BP = 5_000  # 50%
_MIN_AVAILABLE_FOR_RATIO_CHECK = 4
_STABLE_FACT_SLICE = "relevant_facts"


def _truncation_omitted_by_slice(
    entries: Sequence[TruncationEntry],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for entry in entries:
        counts[entry.slice_name] += entry.omitted_count
    return counts


def _available_items(resolved_count: int, compiled: CapsuleSlice) -> int:
    if resolved_count <= 0:
        return 0
    if compiled.availability != "available":
        return resolved_count
    return resolved_count


def assert_context_slice_retention_coverage(
    *,
    capsule: ContextCapsule,
    resolved_counts: Mapping[str, int],
) -> None:
    """Fail closed when compiled slices drop content without audit reasons.

    Examples this gate is meant to catch:

    * ``relevant_facts`` resolves 23 user Facts but compiles to 2 episodic
      items while 6 stable profile/residence Facts vanish with no
      ``TruncationEntry`` — the name Fact existed in the ledger but never
      reached her materials.
    * ``private_impressions`` resolves 3 impressions, compiles 1, and the
      truncation log only records ``field_budget: 1`` — the other gap is
      silent (today's third instance of the same capsule bug class).
    * ``world_life`` marks ``availability=unavailable`` even though every
      item fit individually — a whole-slice discard without per-item
      ``hold_reason``/truncation (today's second instance).

    Full-version note: this check runs post-compile with resolved counts.
    Wiring it into ``PinnedTurnCompiler`` requires every resolver to publish
    per-slice resolved cardinalities on the trusted request; that plumbing is
    not installed yet, so production calls this from tests and audit scripts.
    """

    omitted = _truncation_omitted_by_slice(capsule.budget.truncation_log)
    failures: list[str] = []

    for slice_name, resolved in resolved_counts.items():
        if resolved <= 0:
            continue
        compiled = getattr(capsule, slice_name, None)
        if not isinstance(compiled, CapsuleSlice):
            continue
        kept = len(compiled.items) if compiled.availability == "available" else 0
        audited_drop = omitted.get(slice_name, 0)
        unexplained = resolved - kept - audited_drop
        if unexplained <= 0:
            continue
        if (
            resolved >= _MIN_AVAILABLE_FOR_RATIO_CHECK
            and kept * 10_000 < resolved * _RETENTION_RATIO_BP
            and audited_drop < resolved - kept
        ):
            failures.append(
                f"{slice_name}: resolved={resolved} kept={kept} "
                f"truncation={audited_drop} unexplained={unexplained}"
            )

    stable_available = resolved_counts.get(_STABLE_FACT_SLICE, 0)
    if stable_available:
        fact_slice = capsule.relevant_facts
        if fact_slice.availability == "available":
            kept_stable = sum(
                1
                for item in fact_slice.items
                if fact_predicate_is_stable(
                    __import__("json").loads(item.payload_json).get("predicate_code", "")
                )
            )
        else:
            kept_stable = 0
        # Count stable Facts in resolved set via truncation log is insufficient;
        # callers pass stable_resolved separately when known.
        stable_resolved = resolved_counts.get("relevant_facts_stable", 0)
        if stable_resolved and kept_stable < stable_resolved:
            audited = omitted.get(_STABLE_FACT_SLICE, 0)
            failures.append(
                f"{_STABLE_FACT_SLICE}: stable resolved={stable_resolved} "
                f"kept={kept_stable} truncation={audited}"
            )

    if failures:
        raise AssertionError(
            "context slice retention gaps:\n- " + "\n- ".join(failures)
        )


def explain_slice_retention_gap(
    *,
    slice_name: str,
    resolved: int,
    compiled: CapsuleSlice,
    truncation_log: Sequence[TruncationEntry],
) -> dict[str, int | str]:
    kept = len(compiled.items) if compiled.availability == "available" else 0
    audited = sum(
        entry.omitted_count
        for entry in truncation_log
        if entry.slice_name == slice_name
    )
    return {
        "slice_name": slice_name,
        "resolved": resolved,
        "kept": kept,
        "audited_omitted": audited,
        "unexplained": max(0, resolved - kept - audited),
        "availability": compiled.availability,
    }


_AUDITABLE_TRUNCATION_REASONS: frozenset[TruncationReason] = frozenset(
    {
        "item_budget",
        "field_budget",
        "character_budget",
        "source_envelope_budget",
        "global_character_budget",
        "required_slice_model_fit",
    }
)


def assert_truncation_reasons_are_auditable(
    entries: Sequence[TruncationEntry],
) -> None:
    unknown = sorted(
        {entry.reason for entry in entries if entry.reason not in _AUDITABLE_TRUNCATION_REASONS}
    )
    if unknown:
        raise AssertionError(
            "context capsule truncation uses non-auditable reasons: "
            + ", ".join(unknown)
        )


__all__ = (
    "assert_context_slice_retention_coverage",
    "assert_truncation_reasons_are_auditable",
    "explain_slice_retention_gap",
)
