"""Bounded lossless storage of repeated review pins; no review authority.

Decoded bytes go through the original candidate, source and invocation joins.
The digest detects storage corruption, never substitutes for those checks.
"""
import base64
import hashlib
import json
import zlib

from .visible_review_protocols import CONTENT_FIELD_PROTOCOL

STORAGE_CONTRACT = "visible-source-runtime-evidence.deflate.1"
MAX_STORED_BYTES = 1_048_576
# Requirement, receipt and author request retain their separate original
# bounds. This aggregate includes their repeated JSON-escaped pins.
MAX_DECODED_BYTES = 4_194_304


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object(raw):
    value = json.loads(raw)
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise ValueError("review evidence storage requires canonical object bytes")
    return value


def _require_new_protocol(value):
    if (value.get("contract") != "visible-source-runtime-evidence.3"
        or json.loads(value["requirement_json"]).get("review_protocol") != CONTENT_FIELD_PROTOCOL):
        raise ValueError("compressed review evidence requires its explicit pinned protocol")


def store_review_evidence(raw: str) -> str:
    """Keep small/historical bytes intact; only the new protocol may overflow."""
    data = raw.encode("utf-8")
    if len(data) > MAX_DECODED_BYTES:
        raise ValueError("decoded review evidence exceeds aggregate bound")
    value = _object(raw)
    if len(data) <= MAX_STORED_BYTES:
        return raw
    _require_new_protocol(value)
    stored = _canonical({
        "contract": STORAGE_CONTRACT, "decoded_bytes": len(data),
        "decoded_sha256": hashlib.sha256(data).hexdigest(),
        "data_base64": base64.b64encode(zlib.compress(data)).decode("ascii"),
    })
    if len(stored.encode()) > MAX_STORED_BYTES:
        raise ValueError("stored review evidence exceeds carrier bound")
    return stored


def read_review_evidence(raw: str) -> dict:
    """Decode one complete bounded stream, then return the original object."""
    if not isinstance(raw, str) or len(raw.encode()) > MAX_STORED_BYTES:
        raise ValueError("visible review evidence is missing or oversized")
    value = _object(raw)
    if value.get("contract") != STORAGE_CONTRACT:
        return value
    if set(value) != {"contract", "decoded_bytes", "decoded_sha256", "data_base64"}:
        raise ValueError("compressed review evidence storage fields differ")
    size = value["decoded_bytes"]
    if type(size) is not int or not 1 <= size <= MAX_DECODED_BYTES:
        raise ValueError("compressed review evidence decoded size is invalid")
    if not isinstance(value["data_base64"], str):
        raise ValueError("compressed review evidence body is invalid")
    try:
        compressed = base64.b64decode(value["data_base64"], validate=True)
        stream = zlib.decompressobj()
        # max_length bounds allocation even when the declared size is false.
        data = stream.decompress(compressed, size + 1)
    except (ValueError, zlib.error) as exc:
        raise ValueError("compressed review evidence stream is invalid") from exc
    if (len(data) != size or not stream.eof or stream.unused_data or stream.unconsumed_tail
        or hashlib.sha256(data).hexdigest() != value["decoded_sha256"]):
        raise ValueError("compressed review evidence size, stream or digest differs")
    decoded = _object(data.decode("utf-8"))
    _require_new_protocol(decoded)
    return decoded
