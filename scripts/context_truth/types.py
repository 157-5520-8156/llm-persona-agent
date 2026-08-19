"""Shared records for the context-truth auditor."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, Callable, Literal, Mapping


Relation = Literal[
    "eq",
    "subset",
    "head_included",
    "implies_positive",
    "approx_seconds",
    "monotonic_le",
    "custom",
]

Severity = Literal["critical", "high", "medium", "low", "info"]

KNOWN_ISSUE_IDS = ("K1", "K2", "K3", "K4", "K5", "K6")

KNOWN_ISSUE_TITLES = {
    "K1": "快照可分享 0 张，账本有可用候选",
    "K2": "周记写照片已发出，账本 media_deliveries = 0",
    "K3": "她被告知他回来了，账本里他没说话",
    "K4": "她看不见自己刚说的话，账本里有",
    "K5": "收到 qq-face，只看见编号，目录里有名字",
    "K6": "外部感知处理了多条，她看见 0 条",
}


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


@dataclass
class SeenView:
    """Path A: what the production snapshot / model_view told her."""

    materials: dict[str, Any] = field(default_factory=dict)
    faculties: dict[str, Any] = field(default_factory=dict)
    conversation: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    snapshot_id: str | None = None
    logical_time: datetime | None = None
    cursor_seq: int | None = None
    world_revision: int | None = None

    @classmethod
    def from_model_view(cls, view: Mapping[str, Any]) -> "SeenView":
        materials = view.get("materials")
        materials = dict(materials) if isinstance(materials, dict) else {}
        conversation = materials.get("conversation")
        cursor = view.get("cursor") if isinstance(view.get("cursor"), dict) else {}
        logical = _parse_dt(materials.get("logical_time") or cursor.get("logical_time"))
        seq = cursor.get("ledger_sequence")
        rev = cursor.get("world_revision")
        return cls(
            materials=materials,
            faculties=dict(view.get("faculties") or {})
            if isinstance(view.get("faculties"), dict)
            else {},
            conversation=[item for item in conversation if isinstance(item, str)]
            if isinstance(conversation, list)
            else [],
            source_refs=[item for item in (view.get("source_refs") or ()) if isinstance(item, str)],
            snapshot_id=str(view.get("snapshot_id") or "") or None,
            logical_time=logical,
            cursor_seq=int(seq) if isinstance(seq, int) else None,
            world_revision=int(rev) if isinstance(rev, int) else None,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SeenView":
        if "materials" in data or "faculties" in data:
            materials = data.get("materials")
            materials = dict(materials) if isinstance(materials, dict) else {}
            conversation = data.get("conversation")
            if conversation is None:
                raw = materials.get("conversation")
                conversation = raw if isinstance(raw, list) else []
            logical = _parse_dt(data.get("logical_time") or materials.get("logical_time"))
            return cls(
                materials=materials,
                faculties=dict(data.get("faculties") or {})
                if isinstance(data.get("faculties"), dict)
                else {},
                conversation=[item for item in conversation if isinstance(item, str)],
                source_refs=[
                    item for item in (data.get("source_refs") or ()) if isinstance(item, str)
                ],
                snapshot_id=str(data.get("snapshot_id") or "") or None,
                logical_time=logical,
                cursor_seq=data.get("cursor_seq") if isinstance(data.get("cursor_seq"), int) else None,
                world_revision=(
                    data.get("world_revision")
                    if isinstance(data.get("world_revision"), int)
                    else None
                ),
            )
        return cls(materials=dict(data))


@dataclass
class CounterpartLine:
    event_id: str
    text: str
    occurred_at: datetime | None
    reaction_refs: tuple[str, ...] = ()
    sticker_refs: tuple[str, ...] = ()


@dataclass
class CompanionLine:
    payload_ref: str
    text: str
    state: str
    occurred_at: datetime | None = None


@dataclass
class CatalogFace:
    provider_ref: str
    catalog_name: str | None
    catalog_glyph: str | None
    seen_text: str | None


@dataclass
class LedgerTruth:
    """Path B: equivalent facts taken from the ledger projection / events."""

    world_id: str = ""
    cursor_seq: int = 0
    world_revision: int = 0
    logical_time: datetime | None = None
    available_photo_candidate_ids: tuple[str, ...] = ()
    photo_candidate_status_counts: dict[str, int] = field(default_factory=dict)
    media_delivery_ids: tuple[str, ...] = ()
    last_counterpart: CounterpartLine | None = None
    counterpart_all: tuple[CounterpartLine, ...] = ()
    counterpart_recent: tuple[CounterpartLine, ...] = ()
    companion_all_settled: tuple[CompanionLine, ...] = ()
    companion_recent_settled: tuple[CompanionLine, ...] = ()
    companion_waiting: tuple[CompanionLine, ...] = ()
    catalog_faces: tuple[CatalogFace, ...] = ()
    external_perception_count: int = 0
    perception_result_count: int = 0
    sidecar_processed_count: int | None = None
    active_appraisal_ids: tuple[str, ...] = ()
    expired_appraisal_ids: tuple[str, ...] = ()
    active_affect_ids: tuple[str, ...] = ()
    active_impression_ids: tuple[str, ...] = ()
    open_thread_ids: tuple[str, ...] = ()
    active_aspiration_ids: tuple[str, ...] = ()
    active_plan_ids: tuple[str, ...] = ()
    active_life_arc_ids: tuple[str, ...] = ()
    relationship_stage: str | None = None
    counterpart_subject_ref: str | None = None
    npc_relationship_count: int = 0
    active_memory_count: int = 0
    fact_count: int = 0
    settled_occurrence_count: int = 0
    experience_count: int = 0
    has_character_core: bool = False
    active_non_sleep_plan_ids: tuple[str, ...] = ()
    interaction_act_count: int = 0
    notes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LedgerTruth":
        known = {item.name for item in fields(cls)}
        payload: dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue
            if key == "logical_time":
                payload[key] = _parse_dt(value)
            elif key == "last_counterpart" and isinstance(value, dict):
                payload[key] = CounterpartLine(
                    event_id=str(value.get("event_id") or ""),
                    text=str(value.get("text") or ""),
                    occurred_at=_parse_dt(value.get("occurred_at")),
                    reaction_refs=tuple(value.get("reaction_refs") or ()),
                    sticker_refs=tuple(value.get("sticker_refs") or ()),
                )
            elif key in {"counterpart_recent", "counterpart_all"} and isinstance(value, list):
                payload[key] = tuple(
                    CounterpartLine(
                        event_id=str(item.get("event_id") or ""),
                        text=str(item.get("text") or ""),
                        occurred_at=_parse_dt(item.get("occurred_at")),
                        reaction_refs=tuple(item.get("reaction_refs") or ()),
                        sticker_refs=tuple(item.get("sticker_refs") or ()),
                    )
                    for item in value
                    if isinstance(item, dict)
                )
            elif key in {"companion_recent_settled", "companion_all_settled"} and isinstance(
                value, list
            ):
                payload[key] = tuple(
                    CompanionLine(
                        payload_ref=str(item.get("payload_ref") or ""),
                        text=str(item.get("text") or ""),
                        state=str(item.get("state") or "settled"),
                        occurred_at=_parse_dt(item.get("occurred_at")),
                    )
                    for item in value
                    if isinstance(item, dict)
                )
            elif key == "catalog_faces" and isinstance(value, list):
                payload[key] = tuple(
                    CatalogFace(
                        provider_ref=str(item.get("provider_ref") or ""),
                        catalog_name=item.get("catalog_name")
                        if isinstance(item.get("catalog_name"), str)
                        else None,
                        catalog_glyph=item.get("catalog_glyph")
                        if isinstance(item.get("catalog_glyph"), str)
                        else None,
                        seen_text=item.get("seen_text")
                        if isinstance(item.get("seen_text"), str)
                        else None,
                    )
                    for item in value
                    if isinstance(item, dict)
                )
            elif key.endswith("_ids") and isinstance(value, list):
                payload[key] = tuple(str(item) for item in value)
            else:
                payload[key] = value
        return cls(**payload)


@dataclass(frozen=True)
class SlotSpec:
    """One mechanically comparable world-fact slot.

    HOW TO ADD A SLOT
    -----------------
    1. Give it a stable ``slot_id`` like ``facet.material.field``.
    2. Name every InnerLifeSnapshot facet that presents this fact.
    3. Write ``extract_seen`` against ``SeenView.materials`` / ``model_view``.
       Do not re-derive the snapshot here — Path A already compiled it.
    4. Write ``extract_ledger`` against ``LedgerTruth`` (projection + event
       lookup). Do not read snapshot materials.
    5. Pick a relation:
         eq              counts / enums / timestamps must match
         subset          everything she sees must exist on the ledger
         head_included   the ledger's recent-N must appear in what she sees
         implies_positive  if she is told a claim, the ledger count must be > 0
         approx_seconds  elapsed-time, with ``tolerance_seconds``
         monotonic_le    she may see fewer (budget) but never more
         custom          supply ``compare``
    6. Add a fixture case in ``tests/world_v2/fixtures/context_truth/`` that
       fails this slot. The six known issues (K1–K6) are the coverage gate:
       if a new inventory drop any of them, the unit test fails.
    7. Do not change production compilers to make the auditor pass.
    """

    slot_id: str
    facets: tuple[str, ...]
    material_key: str
    title: str
    relation: Relation
    severity: Severity
    why_it_matters: str
    extract_seen: Callable[[SeenView], Any]
    extract_ledger: Callable[[LedgerTruth], Any]
    known_issue: str | None = None
    tolerance_seconds: int = 5
    compare: Callable[[Any, Any], str | None] | None = None
    notes: str = ""


@dataclass
class Finding:
    severity: Severity
    slot_id: str
    title: str
    facets: tuple[str, ...]
    relation: str
    seen: Any
    ledger: Any
    detail: str
    why_it_matters: str
    known_issue: str | None = None
    kind: str = "mismatch"
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "slot_id": self.slot_id,
            "title": self.title,
            "facets": list(self.facets),
            "relation": self.relation,
            "kind": self.kind,
            "seen": _jsonable(self.seen),
            "ledger": _jsonable(self.ledger),
            "detail": self.detail,
            "why_it_matters": self.why_it_matters,
            "known_issue": self.known_issue,
            "known_issue_title": KNOWN_ISSUE_TITLES.get(self.known_issue or ""),
            "evidence": self.evidence,
        }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)
