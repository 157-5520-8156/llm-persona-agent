"""Rebuildable, source-bound hybrid recall over World-derived documents.

The ledger remains the only factual authority.  This Module stores disposable
search material at one exact projection cursor and returns evidence candidates
with their original source refs.  Retrieval scores affect accessibility only;
they never change validity, authorize an Action, or recommend behaviour.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Literal, Protocol, Self
import unicodedata

from pydantic import Field, model_validator

from .prehistory_memory_source import PrehistoryMemoryReading
from .recall_model_reading import RECALL_INDEX_POLICY_VERSION, interior_recall_item
from .schema_core import FrozenModel, PrivacyClass
from .sqlite_coordination import configure_shared_sqlite_connection, sqlite_write_lock


# Model context and local evidence have different costs. Full immutable proof
# envelopes never enter the role's reading; charging them to its context budget
# silently discards small memories. The complete trace retains its independent
# 32 KB audit limit, including query and request, in RecallAuditTrace.
RECALL_MODEL_READING_MAX_BYTES = 6_000
RECALL_RESULT_MAX_BYTES = 12_000
MAX_RECALL_QUERY_CHARACTERS = 1_024
_PRIVACY_RANK: dict[PrivacyClass, int] = {
    "public": 0,
    "shareable": 1,
    "personal": 2,
    "private": 3,
    "withhold": 4,
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class RecallCursor(FrozenModel):
    world_revision: int = Field(ge=0)
    deliberation_revision: int = Field(ge=0)
    ledger_sequence: int = Field(ge=0)


class RecallSourceBinding(FrozenModel):
    """Immutable authority closure retained by a disposable recall document."""

    source_kind: Literal[
        "committed_event",
        "execution_receipt",
        "immutable_payload",
    ]
    authority_type: str = Field(min_length=1, max_length=128)
    ref: str = Field(min_length=1, max_length=256)
    source_world_revision: int = Field(ge=0)
    immutable_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RecallDocument(FrozenModel):
    """One rebuildable search document retaining its complete source closure."""

    document_id: str = Field(min_length=1, max_length=256)
    memory_kind: Literal["episodic", "semantic", "reflective"]
    source_item_ref: str = Field(min_length=1, max_length=256)
    source_slice: Literal[
        "recent_dialogue",
        "open_threads",
        "relevant_facts",
        "recent_experiences",
        "world_life",
        "active_memory_candidates",
        "recalled_emotional_associations",
        "private_impressions",
    ]
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=16)
    source_bindings: tuple[RecallSourceBinding, ...] = Field(min_length=1, max_length=16)
    source_world_revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=4_096)
    # Optional ontology/search metadata.  ``text`` stays the exact
    # source-bound excerpt; this field only improves accessibility for
    # indirect cues and carries no additional factual authority.
    retrieval_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=4_096,
        # This metadata did not exist in historical durable recall traces.
        # Keep an absent legacy field canonically identical to an explicit
        # default so replay does not change the immutable result hash.
        exclude_if=lambda value: value is None,
    )
    actor_ref: str = Field(min_length=1, max_length=256)
    subject_refs: tuple[str, ...] = Field(min_length=1, max_length=8)
    link_refs: tuple[str, ...] = Field(default=(), max_length=32)
    occurred_from: datetime
    occurred_to: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    status: Literal[
        "active",
        "historical",
        "superseded",
        "contradicted",
        "expired",
        "released",
    ] = "active"
    privacy_class: PrivacyClass
    authority: Literal[
        "world_fact",
        "dialogue_record",
        "defeasible_interpretation",
    ] = "world_fact"
    epistemic_scope: Literal[
        "world_fact",
        "character_prehistory",
        "counterpart_report_only",
        "companion_expression_record",
        "private_interpretation",
    ] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    prehistory: PrehistoryMemoryReading | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    speaker_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def source_and_time_are_closed(self) -> Self:
        for label, refs in (
            ("source refs", self.source_refs),
            ("subject refs", self.subject_refs),
            ("link refs", self.link_refs),
        ):
            if refs != tuple(sorted(set(refs))):
                raise ValueError(f"recall {label} must be sorted and unique")
        bindings = tuple(
            sorted(
                self.source_bindings,
                key=lambda item: (
                    item.source_kind,
                    item.authority_type,
                    item.ref,
                    item.source_world_revision,
                    item.immutable_hash,
                ),
            )
        )
        if self.source_bindings != bindings or len(
            {(item.source_kind, item.ref) for item in bindings}
        ) != len(bindings):
            raise ValueError("recall source bindings must be canonical and unique")
        if self.source_refs != tuple(sorted({item.ref for item in bindings})):
            raise ValueError("recall source refs must exactly match source bindings")
        if self.source_world_revision != max(item.source_world_revision for item in bindings):
            raise ValueError("recall source revision must match its authority closure")
        times = (
            self.occurred_from,
            self.occurred_to,
            self.valid_from,
            self.valid_to,
        )
        if any(
            value is not None and (value.tzinfo is None or value.utcoffset() is None)
            for value in times
        ):
            raise ValueError("recall document times must be timezone-aware")
        if self.occurred_to is not None and self.occurred_to < self.occurred_from:
            raise ValueError("recall occurrence interval is reversed")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("recall validity interval is reversed")
        if (self.memory_kind == "reflective") != (self.authority == "defeasible_interpretation"):
            raise ValueError("reflective recall must remain non-factual authority")
        if self.authority == "dialogue_record":
            if self.epistemic_scope not in {
                "counterpart_report_only",
                "companion_expression_record",
            }:
                raise ValueError("dialogue recall must retain its epistemic scope")
            if self.speaker_ref is None:
                raise ValueError("dialogue recall must retain its speaker")
            if self.speaker_ref not in self.subject_refs:
                raise ValueError("dialogue recall speaker must remain in subject scope")
            if (
                self.epistemic_scope == "counterpart_report_only"
                and self.speaker_ref == self.actor_ref
            ) or (
                self.epistemic_scope == "companion_expression_record"
                and self.speaker_ref != self.actor_ref
            ):
                raise ValueError("dialogue recall speaker contradicts its epistemic scope")
        elif self.speaker_ref is not None:
            raise ValueError("non-dialogue recall cannot declare a dialogue speaker")
        elif (
            self.authority == "world_fact"
            and self.epistemic_scope not in {None, "world_fact", "character_prehistory"}
        ) or (
            self.authority == "defeasible_interpretation"
            and self.epistemic_scope not in {None, "private_interpretation"}
        ):
            raise ValueError("recall authority and epistemic scope disagree")
        if (self.epistemic_scope == "character_prehistory") != (self.prehistory is not None):
            raise ValueError("historical recall requires its separate prehistory scope")
        if self.prehistory is not None:
            scope = self.prehistory
            if (
                self.actor_ref != scope.actor_ref
                or self.occurred_from != scope.occurred_from
                or self.occurred_to != scope.occurred_until
                or self.memory_kind != "episodic"
                or self.status != "active"
                or self.source_slice != "active_memory_candidates"
                or len(bindings) != 2
                or not any(item.authority_type == "CharacterPrehistoryRecordImported"
                           and item.source_kind == "committed_event" for item in bindings)
                or not any(item == RecallSourceBinding(
                    source_kind="committed_event", authority_type="CharacterPrehistoryArchiveAccepted",
                    ref=scope.archive_event_ref, source_world_revision=scope.archive_world_revision,
                    immutable_hash=scope.archive_payload_hash,
                ) for item in bindings)
            ):
                raise ValueError("historical recall changed source ownership, interval or authority")
        return self

    @property
    def effective_epistemic_scope(
        self,
    ) -> Literal[
        "world_fact",
        "character_prehistory",
        "counterpart_report_only",
        "companion_expression_record",
        "private_interpretation",
    ]:
        if self.epistemic_scope is not None:
            return self.epistemic_scope
        return (
            "private_interpretation"
            if self.authority == "defeasible_interpretation"
            else "world_fact"
        )


class RecallQuery(FrozenModel):
    # ``query_text`` is the compact semantic attention cue used by the dense
    # lane. ``lexical_text`` optionally retains the exact inbound wording for
    # lexical matching; historical traces omit it and therefore keep the
    # legacy one-text behaviour and hash.
    query_text: str = Field(min_length=1, max_length=MAX_RECALL_QUERY_CHARACTERS)
    lexical_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_RECALL_QUERY_CHARACTERS,
        exclude_if=lambda value: value is None,
    )
    cursor: RecallCursor
    actor_ref: str = Field(min_length=1, max_length=256)
    subject_refs: tuple[str, ...] = Field(min_length=1, max_length=8)
    viewer_privacy_ceiling: PrivacyClass
    at: datetime
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    link_refs: tuple[str, ...] = Field(default=(), max_length=32)
    memory_kinds: tuple[Literal["episodic", "semantic", "reflective"], ...] = ()
    include_historical: bool = False
    limit: int = Field(default=6, ge=1, le=12)
    accessibility_seed: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def query_is_canonical(self) -> Self:
        if self.subject_refs != tuple(sorted(set(self.subject_refs))):
            raise ValueError("recall query subjects must be sorted and unique")
        if self.link_refs != tuple(sorted(set(self.link_refs))):
            raise ValueError("recall query links must be sorted and unique")
        if self.memory_kinds != tuple(sorted(set(self.memory_kinds))):
            raise ValueError("recall query kinds must be sorted and unique")
        for value in (self.at, self.occurred_from, self.occurred_to):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("recall query times must be timezone-aware")
        if (
            self.occurred_from is not None
            and self.occurred_to is not None
            and self.occurred_to < self.occurred_from
        ):
            raise ValueError("recall query occurrence interval is reversed")
        return self


class RecallHit(FrozenModel):
    document: RecallDocument
    match_channels: tuple[Literal["lexical", "dense", "temporal", "structured"], ...] = Field(
        min_length=1
    )
    score_bp: int = Field(ge=0, le=10_000)
    lexical_score_bp: int = Field(ge=0, le=10_000)
    dense_score_bp: int = Field(ge=0, le=10_000)
    temporal_score_bp: int = Field(ge=0, le=10_000)
    structured_score_bp: int = Field(ge=0, le=10_000)
    accessibility_offset_bp: int = Field(ge=-500, le=500)


class RecallResult(FrozenModel):
    query_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_version: str = Field(min_length=1, max_length=256)
    embedding_version: str = Field(min_length=1, max_length=256)
    embedding_status: Literal["unknown", "used", "degraded"] = "unknown"
    embedding_failure_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    index_cursor: RecallCursor
    query: RecallQuery
    hits: tuple[RecallHit, ...]


class RecallRebuildReport(FrozenModel):
    mode: Literal["noop", "cursor_only", "documents_changed"]
    document_count: int = Field(ge=0)
    sqlite_changes: int = Field(ge=0)


class RecallEmbedding(Protocol):
    version: str
    dimensions: int

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class RecallEmbeddingUnavailable(RuntimeError):
    """Recoverable dense-provider outage; other recall channels remain valid."""


class FeatureHashRecallEmbedding:
    """Zero-network dense feature projection used until semantic vectors exist.

    This adapter is intentionally named for what it is: a deterministic dense
    projection of lexical features, not a semantic model.  Deployments may
    inject a real embedding adapter through ``RecallEmbedding`` without
    changing index, audit, replay, or authority contracts.
    """

    version = "feature-hash-ngram.1"
    dimensions = 256
    dense_match_threshold_bp = 5_500

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            values = [0.0] * self.dimensions
            for feature in _lexical_features(text):
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
                bucket = int.from_bytes(digest[:8], "big") % self.dimensions
                sign = 1.0 if digest[8] & 1 else -1.0
                values[bucket] += sign
            vectors.append(tuple(values))
        return tuple(vectors)


class RecallIndex(Protocol):
    def rebuild(
        self,
        *,
        cursor: RecallCursor,
        documents: tuple[RecallDocument, ...],
    ) -> RecallRebuildReport: ...

    def search(self, query: RecallQuery) -> RecallResult: ...

    def snapshot(self) -> "RecallIndexSnapshot": ...


class _RecallIndexCore:
    def __init__(self, *, embedding: RecallEmbedding) -> None:
        if not embedding.version or not 1 <= embedding.dimensions <= 4_096:
            raise ValueError("recall embedding identity or dimensions are invalid")
        self._embedding = embedding
        self._dense_match_threshold_bp = int(getattr(embedding, "dense_match_threshold_bp", 5_500))
        if not 0 <= self._dense_match_threshold_bp <= 10_000:
            raise ValueError("recall dense match threshold is invalid")
        self._index_version = f"{RECALL_INDEX_POLICY_VERSION}+embedding:{embedding.version}"
        self._last_rebuild_embedding_failure: str | None = None

    def _materialize(
        self,
        *,
        cursor: RecallCursor,
        documents: tuple[RecallDocument, ...],
    ) -> tuple[tuple[RecallDocument, tuple[float, ...]], ...]:
        ordered = tuple(sorted(documents, key=lambda item: item.document_id))
        if len({item.document_id for item in ordered}) != len(ordered):
            raise ValueError("recall document identity is duplicated")
        if any(item.source_world_revision > cursor.world_revision for item in ordered):
            raise ValueError("recall document is newer than the index cursor")
        try:
            vectors = self._embedding.embed(
                tuple(item.retrieval_text or item.text for item in ordered)
            )
        except RecallEmbeddingUnavailable as exc:
            vectors = tuple((0.0,) * self._embedding.dimensions for _ in ordered)
            self._last_rebuild_embedding_failure = str(exc)[:128] or "embedding_unavailable"
        else:
            self._last_rebuild_embedding_failure = None
        if len(vectors) != len(ordered):
            raise ValueError("recall embedding count does not match documents")
        normalized = tuple(self._normalize_vector(value) for value in vectors)
        return tuple(zip(ordered, normalized, strict=True))

    def _search(
        self,
        *,
        query: RecallQuery,
        cursor: RecallCursor,
        rows: tuple[tuple[RecallDocument, tuple[float, ...]], ...],
    ) -> RecallResult:
        if query.cursor != cursor:
            raise ValueError("recall query cursor does not match the sidecar cursor")
        embedding_failure = self._last_rebuild_embedding_failure
        try:
            query_vector_values = self._embedding.embed((query.query_text,))
        except RecallEmbeddingUnavailable as exc:
            query_vector_values = ((0.0,) * self._embedding.dimensions,)
            embedding_failure = str(exc)[:128] or "embedding_unavailable"
        if len(query_vector_values) != 1:
            raise ValueError("recall query embedding count is invalid")
        query_vector = self._normalize_vector(query_vector_values[0])
        lexical_text = query.lexical_text or query.query_text
        eligible_rows = tuple((document, vector) for document, vector in rows
                              if self._eligible(document, query))
        lexical_scores = _corpus_lexical_scores(lexical_text, tuple(
            _lexical_features(document.retrieval_text or document.text)
            for document, _ in eligible_rows
        ))
        ranked: list[tuple[int, str, RecallHit]] = []
        for (document, vector), lexical in zip(eligible_rows, lexical_scores, strict=True):
            dense = max(0, min(10_000, round(_cosine(query_vector, vector) * 10_000)))
            structured = _structured_score(query.link_refs, document.link_refs)
            temporal = _temporal_score(query, document)
            channels: list[Literal["lexical", "dense", "temporal", "structured"]] = []
            # A remembered entity can be a two-character island inside a
            # natural sentence ("新开的咖啡馆" recalling a prior coffee
            # reaction). Query-coverage thresholds discard exactly those
            # human-like associative cues as the surrounding message grows.
            # Any real normalized n-gram overlap may enter the bounded
            # candidate set; its small score still ranks below stronger
            # lexical, dense and structured matches.
            if lexical > 0:
                channels.append("lexical")
            if dense >= self._dense_match_threshold_bp:
                channels.append("dense")
            if structured:
                channels.append("structured")
            # Time constrains and explains an otherwise matched recollection;
            # recency alone must never retrieve unrelated material.
            if temporal and (query.occurred_from is not None or query.occurred_to is not None):
                channels.append("temporal")
            if not any(channel in channels for channel in ("lexical", "dense", "structured")):
                continue
            accessibility = _accessibility_offset(
                seed=query.accessibility_seed,
                document_id=document.document_id,
            )
            # Keep raw dense similarity in the audit, but below-threshold
            # values are not evidence in the fused ranking. Apply the integer
            # accessibility draw after rounding so it remains exactly additive.
            score = max(
                0,
                min(
                    10_000,
                    round(
                        lexical * 0.35
                        + (dense * 0.35 if "dense" in channels else 0)
                        + structured * 0.20
                        + temporal * 0.10
                    ) + accessibility,
                ),
            )
            hit = RecallHit(
                document=document,
                match_channels=tuple(channels),
                score_bp=score,
                lexical_score_bp=lexical,
                dense_score_bp=dense,
                temporal_score_bp=temporal,
                structured_score_bp=structured,
                accessibility_offset_bp=accessibility,
            )
            ranked.append((score, document.document_id, hit))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected: list[RecallHit] = []

        def add_if_within_budget(hit: RecallHit) -> bool:
            if len(selected) >= query.limit:
                return False
            candidate = (*selected, hit)
            if len(_canonical_json({"items": [
                interior_recall_item(item.document, index_version=self._index_version)
                for item in candidate
            ]}).encode("utf-8")) > RECALL_MODEL_READING_MAX_BYTES:
                return False
            if (
                len(
                    _canonical_json([item.model_dump(mode="json") for item in candidate]).encode(
                        "utf-8"
                    )
                )
                > RECALL_RESULT_MAX_BYTES
            ):
                return False
            selected.append(hit)
            return True

        # The first result keeps the fused ranking. Subsequent candidates
        # should add independent query evidence rather than repeat the same
        # phrase through another source lane. Source/type diversity breaks
        # ties when cues are already covered (including semantic-only recall).
        # Eligibility and both byte budgets still apply before each selection.
        query_spans = _lexical_feature_spans(lexical_text)
        groups_by_id = {
            hit.document.document_id: _matched_query_groups(
                query_spans, _lexical_features(hit.document.retrieval_text or hit.document.text),
            ) for _, _, hit in ranked
        }
        covered_spans: list[tuple[int, int]] = []
        covered_terms: set[str] = set()
        seen_kinds: set[str] = set()
        seen_slices: set[str] = set()
        seen_subjects: set[str] = set()
        remaining = list(ranked)

        def novelty(hit: RecallHit) -> tuple[int, int]:
            document = hit.document
            novel_groups = [
                terms for start, end, terms in groups_by_id[document.document_id]
                if not terms & covered_terms
                and not any(start < old_end and old_start < end for old_start, old_end in covered_spans)
            ]
            # Repeated wording does not create additional independent cues.
            cue_count = len(set(novel_groups))
            axes = (int(document.memory_kind not in seen_kinds)
                    + int(document.source_slice not in seen_slices)
                    + int(bool(set(document.subject_refs) - seen_subjects)))
            return cue_count, axes

        while remaining and len(selected) < query.limit:
            if selected:
                remaining.sort(key=lambda item: (
                    -novelty(item[2])[0], -novelty(item[2])[1], -item[0], item[1],
                ))
            _, _, hit = remaining.pop(0)
            if not add_if_within_budget(hit):
                continue
            document = hit.document
            seen_kinds.add(document.memory_kind)
            seen_slices.add(document.source_slice)
            seen_subjects.update(document.subject_refs)
            for start, end, terms in groups_by_id[document.document_id]:
                covered_spans.append((start, end))
                covered_terms.update(terms)
        hits = tuple(selected)
        query_hash = recall_query_hash(
            index_version=self._index_version,
            query=query,
        )
        result_hash = recall_result_hash(
            query_hash=query_hash,
            cursor=cursor,
            hit_values=[item.model_dump(mode="json") for item in hits],
        )
        return RecallResult(
            query_hash=query_hash,
            result_hash=result_hash,
            index_version=self._index_version,
            embedding_version=self._embedding.version,
            embedding_status=("degraded" if embedding_failure is not None else "used"),
            embedding_failure_code=embedding_failure,
            index_cursor=cursor,
            query=query,
            hits=hits,
        )

    def _normalize_vector(self, value: tuple[float, ...]) -> tuple[float, ...]:
        if len(value) != self._embedding.dimensions:
            raise ValueError("recall embedding dimensions do not match the adapter")
        if any(not isinstance(item, (int, float)) or not math.isfinite(item) for item in value):
            raise ValueError("recall embedding contains a non-finite value")
        magnitude = math.sqrt(sum(float(item) ** 2 for item in value))
        if magnitude <= 0:
            return tuple(0.0 for _ in value)
        return tuple(round(float(item) / magnitude, 12) for item in value)

    @staticmethod
    def _eligible(document: RecallDocument, query: RecallQuery) -> bool:
        if document.actor_ref != query.actor_ref:
            return False
        if not set(document.subject_refs) & set(query.subject_refs):
            return False
        if _PRIVACY_RANK[document.privacy_class] > _PRIVACY_RANK[query.viewer_privacy_ceiling]:
            return False
        if query.memory_kinds and document.memory_kind not in query.memory_kinds:
            return False
        if not query.include_historical and document.status not in {"active", "released"}:
            return False
        if (
            document.valid_from is not None
            and query.at < document.valid_from
            and not query.include_historical
        ):
            return False
        if (
            document.valid_to is not None
            and query.at >= document.valid_to
            and not query.include_historical
        ):
            return False
        document_end = document.occurred_to or document.occurred_from
        if query.occurred_from is not None and document_end < query.occurred_from:
            return False
        if query.occurred_to is not None and document.occurred_from > query.occurred_to:
            return False
        return True


class RecallIndexSnapshot:
    """Immutable process-local search view for one exact ledger cursor."""

    __slots__ = ("_core", "_cursor", "_rows")

    def __init__(
        self,
        *,
        core: _RecallIndexCore,
        cursor: RecallCursor,
        rows: tuple[tuple[RecallDocument, tuple[float, ...]], ...],
    ) -> None:
        self._core = core
        self._cursor = cursor
        self._rows = rows

    @property
    def cursor(self) -> RecallCursor:
        return self._cursor

    @property
    def documents(self) -> tuple[RecallDocument, ...]:
        return tuple(document for document, _ in self._rows)

    def search(self, query: RecallQuery) -> RecallResult:
        return self._core._search(
            query=query,
            cursor=self._cursor,
            rows=self._rows,
        )


class InMemoryRecallIndex(_RecallIndexCore):
    def __init__(self, *, embedding: RecallEmbedding) -> None:
        super().__init__(embedding=embedding)
        self._cursor: RecallCursor | None = None
        self._rows: tuple[tuple[RecallDocument, tuple[float, ...]], ...] = ()

    def rebuild(
        self,
        *,
        cursor: RecallCursor,
        documents: tuple[RecallDocument, ...],
    ) -> RecallRebuildReport:
        materialized = self._materialize(cursor=cursor, documents=documents)
        mode: Literal["noop", "cursor_only", "documents_changed"]
        if self._cursor == cursor and self._rows == materialized:
            mode = "noop"
        elif self._rows == materialized:
            mode = "cursor_only"
        else:
            mode = "documents_changed"
        self._rows = materialized
        self._cursor = cursor
        return RecallRebuildReport(
            mode=mode,
            document_count=len(materialized),
            sqlite_changes=0,
        )

    def search(self, query: RecallQuery) -> RecallResult:
        if self._cursor is None:
            raise ValueError("recall index has not been built")
        return self._search(query=query, cursor=self._cursor, rows=self._rows)

    def snapshot(self) -> RecallIndexSnapshot:
        if self._cursor is None:
            raise ValueError("recall index has not been built")
        return RecallIndexSnapshot(
            core=self,
            cursor=self._cursor,
            rows=self._rows,
        )


class SQLiteRecallIndex(_RecallIndexCore):
    """Disposable SQLite adapter; deleting its rows loses no World authority."""

    def __init__(
        self,
        *,
        path: str | Path,
        world_id: str,
        embedding: RecallEmbedding,
    ) -> None:
        super().__init__(embedding=embedding)
        if not world_id:
            raise ValueError("SQLite recall index requires a world")
        self._world_id = world_id
        self._path = str(path)
        self._write_lock = sqlite_write_lock(self._path)
        self._connection = sqlite3.connect(
            self._path,
            isolation_level=None,
            check_same_thread=False,
        )
        with self._write_lock:
            configure_shared_sqlite_connection(self._connection)
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS world_v2_recall_index_heads (
                    world_id TEXT PRIMARY KEY,
                    world_revision INTEGER NOT NULL,
                    deliberation_revision INTEGER NOT NULL,
                    ledger_sequence INTEGER NOT NULL,
                    index_version TEXT NOT NULL,
                    document_set_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS world_v2_recall_documents (
                    world_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    memory_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    privacy_rank INTEGER NOT NULL,
                    occurred_from TEXT NOT NULL,
                    occurred_to TEXT,
                    PRIMARY KEY (world_id, document_id)
                );
                CREATE INDEX IF NOT EXISTS world_v2_recall_document_filter
                ON world_v2_recall_documents(
                    world_id, status, memory_kind, privacy_rank, occurred_from
                );
                """
            )

    def rebuild(
        self,
        *,
        cursor: RecallCursor,
        documents: tuple[RecallDocument, ...],
    ) -> RecallRebuildReport:
        head = self._connection.execute(
            """
            SELECT world_revision, deliberation_revision, ledger_sequence,
                   index_version, document_set_hash
            FROM world_v2_recall_index_heads WHERE world_id = ?
            """,
            (self._world_id,),
        ).fetchone()
        reusable: dict[str, tuple[str, tuple[float, ...]]] = {}
        if head is not None and str(head[3]) == self._index_version:
            reusable = {
                str(document_id): (
                    str(document_json),
                    tuple(float(item) for item in json.loads(str(embedding_json))),
                )
                for document_id, document_json, embedding_json in self._connection.execute(
                    """
                    SELECT document_id, document_json, embedding_json
                    FROM world_v2_recall_documents
                    WHERE world_id = ?
                    """,
                    (self._world_id,),
                ).fetchall()
            }
        ordered = tuple(sorted(documents, key=lambda item: item.document_id))
        if len({item.document_id for item in ordered}) != len(ordered):
            raise ValueError("recall document identity is duplicated")
        if any(item.source_world_revision > cursor.world_revision for item in ordered):
            raise ValueError("recall document is newer than the index cursor")
        fresh = tuple(
            item
            for item in ordered
            if (
                reusable.get(item.document_id, ("", ()))[0]
                != _canonical_json(item.model_dump(mode="json"))
                # A recoverable provider outage materializes a zero vector so
                # lexical/temporal/structured recall remains available.  Do
                # not cache that degraded vector as if it were a completed
                # semantic embedding; the next refresh gets another chance.
                or not any(reusable.get(item.document_id, ("", ()))[1])
            )
        )
        fresh_rows = {
            item.document_id: vector
            for item, vector in self._materialize(cursor=cursor, documents=fresh)
        }
        rows = tuple(
            (
                item,
                (
                    reusable[item.document_id][1]
                    if item.document_id in reusable
                    and reusable[item.document_id][0]
                    == _canonical_json(item.model_dump(mode="json"))
                    else fresh_rows[item.document_id]
                ),
            )
            for item in ordered
        )
        if any(
            len(vector) != self._embedding.dimensions
            or any(not math.isfinite(value) for value in vector)
            for _, vector in rows
        ):
            raise ValueError("cached recall embedding is incompatible with its adapter")
        set_hash = _digest(
            [
                {
                    "document": document.model_dump(mode="json"),
                    "embedding": vector,
                }
                for document, vector in rows
            ]
        )
        exact_cursor = (
            cursor.world_revision,
            cursor.deliberation_revision,
            cursor.ledger_sequence,
        )
        if (
            head is not None
            and (
                int(head[0]),
                int(head[1]),
                int(head[2]),
            )
            == exact_cursor
            and str(head[3]) == self._index_version
            and str(head[4]) == set_hash
        ):
            return RecallRebuildReport(
                mode="noop",
                document_count=len(rows),
                sqlite_changes=0,
            )
        changes_before = self._connection.total_changes
        with self._write_lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                documents_changed = (
                    head is None or str(head[3]) != self._index_version or str(head[4]) != set_hash
                )
                if documents_changed:
                    retained_ids = tuple(document.document_id for document, _ in rows)
                    if retained_ids:
                        placeholders = ",".join("?" for _ in retained_ids)
                        self._connection.execute(
                            f"""
                            DELETE FROM world_v2_recall_documents
                            WHERE world_id = ? AND document_id NOT IN ({placeholders})
                            """,
                            (self._world_id, *retained_ids),
                        )
                    else:
                        self._connection.execute(
                            "DELETE FROM world_v2_recall_documents WHERE world_id = ?",
                            (self._world_id,),
                        )
                    self._connection.executemany(
                        """
                        INSERT INTO world_v2_recall_documents(
                            world_id, document_id, document_json, embedding_json,
                            memory_kind, status, privacy_rank, occurred_from, occurred_to
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(world_id, document_id) DO UPDATE SET
                            document_json=excluded.document_json,
                            embedding_json=excluded.embedding_json,
                            memory_kind=excluded.memory_kind,
                            status=excluded.status,
                            privacy_rank=excluded.privacy_rank,
                            occurred_from=excluded.occurred_from,
                            occurred_to=excluded.occurred_to
                        WHERE document_json != excluded.document_json
                           OR embedding_json != excluded.embedding_json
                        """,
                        (
                            (
                                self._world_id,
                                document.document_id,
                                _canonical_json(document.model_dump(mode="json")),
                                _canonical_json(vector),
                                document.memory_kind,
                                document.status,
                                _PRIVACY_RANK[document.privacy_class],
                                document.occurred_from.isoformat(),
                                (
                                    document.occurred_to.isoformat()
                                    if document.occurred_to is not None
                                    else None
                                ),
                            )
                            for document, vector in rows
                        ),
                    )
                self._connection.execute(
                    """
                    INSERT INTO world_v2_recall_index_heads(
                        world_id, world_revision, deliberation_revision,
                        ledger_sequence, index_version, document_set_hash
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(world_id) DO UPDATE SET
                        world_revision=excluded.world_revision,
                        deliberation_revision=excluded.deliberation_revision,
                        ledger_sequence=excluded.ledger_sequence,
                        index_version=excluded.index_version,
                        document_set_hash=excluded.document_set_hash
                    """,
                    (
                        self._world_id,
                        cursor.world_revision,
                        cursor.deliberation_revision,
                        cursor.ledger_sequence,
                        self._index_version,
                        set_hash,
                    ),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return RecallRebuildReport(
            mode=(
                "documents_changed"
                if head is None or str(head[3]) != self._index_version or str(head[4]) != set_hash
                else "cursor_only"
            ),
            document_count=len(rows),
            sqlite_changes=self._connection.total_changes - changes_before,
        )

    def search(self, query: RecallQuery) -> RecallResult:
        return self.snapshot().search(query)

    def snapshot(self) -> RecallIndexSnapshot:
        head = self._connection.execute(
            """
            SELECT world_revision, deliberation_revision, ledger_sequence, index_version
            FROM world_v2_recall_index_heads WHERE world_id = ?
            """,
            (self._world_id,),
        ).fetchone()
        if head is None:
            raise ValueError("recall index has not been built")
        cursor = RecallCursor(
            world_revision=int(head[0]),
            deliberation_revision=int(head[1]),
            ledger_sequence=int(head[2]),
        )
        if str(head[3]) != self._index_version:
            raise ValueError("recall index adapter version does not match stored rows")
        stored = self._connection.execute(
            """
            SELECT document_json, embedding_json
            FROM world_v2_recall_documents
            WHERE world_id = ?
            ORDER BY document_id
            """,
            (self._world_id,),
        ).fetchall()
        rows = tuple(
            (
                RecallDocument.model_validate_json(str(document_json)),
                tuple(float(item) for item in json.loads(str(embedding_json))),
            )
            for document_json, embedding_json in stored
        )
        return RecallIndexSnapshot(core=self, cursor=cursor, rows=rows)

    def close(self) -> None:
        self._connection.close()
        close_embedding = getattr(self._embedding, "close", None)
        if callable(close_embedding):
            close_embedding()


def _lexical_feature_spans(text: str) -> tuple[tuple[int, int, str], ...]:
    """The existing normalized features with their exact query coordinates."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    spans: list[tuple[int, int, str]] = []
    run_start = 0
    kind: str | None = None

    def flush(end: int) -> None:
        if kind == "cjk":
            for width in (2, 3):
                spans.extend((offset, offset + width, normalized[offset:offset + width])
                             for offset in range(run_start, end - width + 1))
        elif kind == "word" and end - run_start >= 3:
            spans.append((run_start, end, normalized[run_start:end]))

    for offset, character in enumerate(normalized):
        codepoint = ord(character)
        next_kind = (
            "cjk"
            if (
                0x3400 <= codepoint <= 0x4DBF
                or 0x4E00 <= codepoint <= 0x9FFF
                or 0x3040 <= codepoint <= 0x30FF
            )
            else "word"
            if character.isalnum()
            else None
        )
        if next_kind != kind:
            flush(offset)
            run_start, kind = offset, next_kind
    flush(len(normalized))
    return tuple(spans)


def _lexical_features(text: str) -> frozenset[str]:
    return frozenset(term for _, _, term in _lexical_feature_spans(text))


def _matched_query_groups(
    spans: tuple[tuple[int, int, str], ...], document: frozenset[str],
) -> tuple[tuple[int, int, frozenset[str]], ...]:
    """Connected overlapping matches; adjacent independent spans stay separate."""
    groups: list[tuple[int, int, frozenset[str]]] = []
    start, end, terms = -1, -1, set()
    for left, right, term in sorted(span for span in spans if span[2] in document):
        if left >= end:
            if terms:
                groups.append((start, end, frozenset(terms)))
            start, terms = left, set()
        end = max(end, right)
        terms.add(term)
    if terms:
        groups.append((start, end, frozenset(terms)))
    return tuple(groups)


def _independent_match_terms(
    spans: tuple[tuple[int, int, str], ...], document: frozenset[str], weights: dict[str, float],
) -> frozenset[str]:
    """One rarity contribution per overlapping query span, without stopwords.

    Repeated copies of a cue also count once. The strongest term represents
    each connected overlap group; no phrase is assigned semantic importance.
    """
    return frozenset(max(terms, key=lambda term: (weights[term], len(term), term))
                     for _, _, terms in _matched_query_groups(spans, document))


def _corpus_lexical_scores(
    query_text: str, documents: tuple[frozenset[str], ...],
) -> tuple[int, ...]:
    """Saturating independent-cue rarity plus exact query coverage.

    Overlapping n-grams from one phrase are correlated evidence. Count the
    strongest match per overlap group, preserving full-query coverage and
    distinct separated cues. Frequencies use eligible documents only.
    """
    spans = _lexical_feature_spans(query_text)
    query = frozenset(term for _, _, term in spans)
    frequencies = {term: sum(term in document for document in documents) for term in query}
    weights = {term: math.log1p((len(documents) - count + 0.5) / (count + 0.5))
               for term, count in frequencies.items() if count}
    query_weight = max(1, sum(len(term) ** 2 for term in query))
    scores = []
    for document in documents:
        overlap = query & document
        independent = _independent_match_terms(spans, document, weights)
        evidence = sum(weights[term] for term in sorted(independent))
        coverage = sum(len(term) ** 2 for term in overlap) / query_weight
        scores.append(round(10_000 * max(coverage, evidence / (evidence + 1.0))))
    return tuple(scores)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("recall vectors use different dimensions")
    return sum(a * b for a, b in zip(left, right, strict=True))


def _structured_score(query_refs: tuple[str, ...], document_refs: tuple[str, ...]) -> int:
    if not query_refs or not document_refs:
        return 0
    overlap = len(set(query_refs) & set(document_refs))
    return min(10_000, round(overlap / len(set(query_refs)) * 10_000))


def _temporal_score(query: RecallQuery, document: RecallDocument) -> int:
    end = document.occurred_to or document.occurred_from
    if query.occurred_from is not None and end < query.occurred_from:
        return 0
    if query.occurred_to is not None and document.occurred_from > query.occurred_to:
        return 0
    if document.prehistory is not None:
        # A historical dating window is not evidence that the remembered
        # occurrence happened at its newest possible instant. Average the
        # existing accessibility decay over that window; this is an index
        # prior, never a new occurrence date or a character decision.
        return _historical_window_accessibility(
            youngest_days=max(0.0, (query.at - end).total_seconds() / 86_400),
            oldest_days=max(0.0, (query.at - document.occurred_from).total_seconds() / 86_400),
        )
    distance_seconds = max(
        0.0,
        (query.at - end).total_seconds(),
    )
    # Smooth 30-day accessibility decay; validity is handled separately.
    return max(500, round(10_000 / (1 + distance_seconds / (30 * 86_400))))


def _historical_window_accessibility(*, youngest_days: float, oldest_days: float) -> int:
    """Uniform window average of max(500, 10000 / (1 + age / 30)).

    The uniform prior expresses absent finer timing information, not a claim
    about when the event occurred. Point dates retain the previous policy.
    log1p of the interval width avoids subtracting nearly equal logarithms.
    """
    young, old = youngest_days / 30, oldest_days / 30
    width = old - young
    if width <= 1e-9:
        return max(500, round(10_000 / (1 + young)))
    # The existing curve reaches its 500 floor at age 570 days (19 units).
    curved_width = min(old, 19.0) - min(young, 19.0)
    area = 10_000 * math.log1p(curved_width / (1 + min(young, 19.0)))
    area += 500 * max(0.0, old - max(young, 19.0))
    return max(500, min(10_000, round(area / width)))


def _accessibility_offset(*, seed: str, document_id: str) -> int:
    raw = hashlib.sha256(
        _canonical_json(
            {
                "contract": "recall-accessibility-draw.1",
                "seed": seed,
                "document_id": document_id,
            }
        ).encode("utf-8")
    ).digest()
    return int.from_bytes(raw[:2], "big") % 401 - 200


def recall_query_hash(*, index_version: str, query: RecallQuery) -> str:
    return _digest(
        {
            "contract": "world-v2-recall-query.1",
            "index_version": index_version,
            "query": query.model_dump(mode="json"),
        }
    )


def recall_result_hash(
    *,
    query_hash: str,
    cursor: RecallCursor,
    hit_values: list[dict[str, object]],
) -> str:
    return _digest(
        {
            "query_hash": query_hash,
            "cursor": cursor.model_dump(mode="json"),
            "hits": hit_values,
        }
    )


__all__ = [
    "InMemoryRecallIndex",
    "RECALL_INDEX_POLICY_VERSION",
    "RECALL_RESULT_MAX_BYTES",
    "RECALL_MODEL_READING_MAX_BYTES",
    "RecallCursor",
    "RecallDocument",
    "RecallEmbedding",
    "RecallEmbeddingUnavailable",
    "RecallHit",
    "RecallIndexSnapshot",
    "RecallQuery",
    "RecallResult",
    "SQLiteRecallIndex",
    "recall_query_hash",
    "recall_result_hash",
]
