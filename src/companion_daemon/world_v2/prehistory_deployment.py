"""Explicit deployment of source-bound fictional backstory."""
from pathlib import Path

from pydantic import TypeAdapter

from .character_prehistory import CompiledPrehistoryArchive, ReviewedPrehistoryArchive

PrehistoryPackage = ReviewedPrehistoryArchive | CompiledPrehistoryArchive

# Offline source/context receipts, independent of unchanged model-input budgets.
MAX_PREHISTORY_PACKAGE_BYTES = 8_000_000


def configured_prehistory(*, path: Path | None, supplied: PrehistoryPackage | None,
                         world_id: str, actor_ref: str) -> PrehistoryPackage | None:
    if path is None:
        archive = supplied
    else:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_PREHISTORY_PACKAGE_BYTES + 1)
        if len(raw) > MAX_PREHISTORY_PACKAGE_BYTES:
            raise ValueError("prehistory package exceeds deployment size limit")
        archive = TypeAdapter(PrehistoryPackage).validate_json(raw)
        if supplied is not None and supplied != archive:
            raise ValueError("explicit and configured prehistory packages disagree")
    if archive is not None and (
        archive.document.world_id != world_id or archive.document.actor_ref != actor_ref
    ):
        raise ValueError("configured prehistory belongs to another World or actor")
    return archive
