#!/usr/bin/env python3
"""OpenAI-compatible local embedding server (MLX + bge).

Exposes POST /v1/embeddings so the daemon's OpenAICompatibleRecallEmbedding
can point at it via WORLD_V2_RECALL_EMBEDDING_BASE_URL without code changes.
Vectors are returned raw; recall_index normalizes them before cosine search.

The production client always sends ``input`` as a list (up to 64 texts, all
recall documents plus the query), includes ``dimensions``, and times out at
10s.  Inference therefore runs off the event loop, under a single MLX lock,
with an in-process cache and a compute cap so a padded batch cannot blow
that budget.
"""
from __future__ import annotations

import argparse
import asyncio
import threading
import time
from typing import Any

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, Field

import mlx_embeddings as me

app = FastAPI(title="girl-agent-local-embeddings")
_MODEL: Any = None
_TOKENIZER: Any = None
_MODEL_NAME = ""
_DIM = 0
_INFER_LOCK = threading.Lock()
_VECTOR_CACHE: dict[str, list[float]] = {}
# n * max_length kept low enough that a cold 40–64 doc batch finishes
# inside the client's 10s ReadTimeout (measured 41×512 ≈ 14s).
_COMPUTE_BUDGET = 10_000
_MAX_LENGTH_CAP = 512


class EmbedRequest(BaseModel):
    model: str = ""
    input: str | list[str] = Field(...)
    dimensions: int | None = None


class EmbeddingData(BaseModel):
    object: str = "embedding"
    index: int = 0
    embedding: list[float]


def _l2_normalize(rows: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    return rows / np.maximum(norms, 1e-9)


def _vectors_from_hidden(out: Any) -> list[list[float]]:
    # bge family uses the [CLS] token as the sentence vector.
    cls = out.last_hidden_state[:, 0, :]
    arr = np.asarray(cls.tolist(), dtype="float64")
    arr = _l2_normalize(arr)
    return [row.tolist() for row in arr]


def _max_length_for(texts: list[str]) -> int:
    longest = max((len(text) for text in texts), default=1)
    budgeted = max(32, _COMPUTE_BUDGET // max(len(texts), 1))
    return int(min(_MAX_LENGTH_CAP, max(32, longest + 8), budgeted))


def _infer_uncached(texts: list[str]) -> list[list[float]]:
    """Run MLX for texts that are not yet in ``_VECTOR_CACHE``. Caller holds lock."""
    if not texts:
        return []
    # Bucket by coarse length so a single long document cannot pad a short
    # batch out to 512 tokens.
    indexed = sorted(enumerate(texts), key=lambda item: len(item[1]))
    pending: list[list[float] | None] = [None] * len(texts)
    bucket: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal bucket
        if not bucket:
            return
        group = [text for _, text in bucket]
        max_length = _max_length_for(group)
        out = me.generate(
            _MODEL,
            _TOKENIZER,
            group,
            max_length=max_length,
            padding=True,
            truncation=True,
        )
        vectors = _vectors_from_hidden(out)
        for (idx, text), vector in zip(bucket, vectors, strict=True):
            _VECTOR_CACHE[text] = vector
            pending[idx] = vector
        bucket = []

    for idx, text in indexed:
        if bucket and (
            len(bucket) >= 16
            or len(text) > 4 * max(len(bucket[0][1]), 1)
        ):
            flush()
        bucket.append((idx, text))
    flush()
    return [item for item in pending if item is not None]


def _embed_texts(texts: list[str]) -> tuple[list[list[float]], dict[str, int]]:
    t0 = time.perf_counter()
    with _INFER_LOCK:
        hits = sum(1 for text in texts if text in _VECTOR_CACHE)
        missing = [text for text in texts if text not in _VECTOR_CACHE]
        # Unique while preserving order — identical docs share one forward pass.
        unique_missing = list(dict.fromkeys(missing))
        if unique_missing:
            inferred = _infer_uncached(unique_missing)
            if len(inferred) != len(unique_missing):
                raise RuntimeError("embedding infer count mismatch")
        vectors = [_VECTOR_CACHE[text] for text in texts]
    stats = {
        "n": len(texts),
        "chars": sum(len(text) for text in texts),
        "cache_hits": hits,
        "inferred": len(unique_missing),
        "ms": int((time.perf_counter() - t0) * 1000),
    }
    return vectors, stats


@app.post("/v1/embeddings")
async def embeddings(req: EmbedRequest) -> dict[str, Any]:
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    if req.dimensions not in (None, 0, _DIM):
        print(
            f"embed ignore dimensions={req.dimensions} (server dim={_DIM})",
            flush=True,
        )
    if not texts:
        return {
            "object": "list",
            "data": [],
            "model": _MODEL_NAME,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }
    vectors, stats = await asyncio.to_thread(_embed_texts, texts)
    print(
        f"embed n={stats['n']} chars={stats['chars']} "
        f"cache_hits={stats['cache_hits']} inferred={stats['inferred']} "
        f"t_ms={stats['ms']}",
        flush=True,
    )
    data = [
        EmbeddingData(index=i, embedding=vectors[i])
        for i in range(len(vectors))
    ]
    prompt_tokens = max(stats["chars"], len(texts))
    return {
        "object": "list",
        "data": [d.model_dump() for d in data],
        "model": _MODEL_NAME,
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="mlx-community/bge-m3")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8190)
    args = parser.parse_args()

    import uvicorn

    global _MODEL, _TOKENIZER, _MODEL_NAME, _DIM
    print(f"loading {args.model} ...", flush=True)
    _MODEL, _TOKENIZER = me.load(args.model)
    _MODEL_NAME = args.model
    probe = me.generate(_MODEL, _TOKENIZER, ["ping"])
    _DIM = int(probe.last_hidden_state.shape[-1])
    print(
        f"READY model={args.model} dim={_DIM} on "
        f"http://{args.host}:{args.port}/v1/embeddings",
        flush=True,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
