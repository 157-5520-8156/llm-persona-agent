"""Explicit deployment of source-bound fictional backstory."""
from pathlib import Path

from pydantic import TypeAdapter

from .character_prehistory import CompiledPrehistoryArchive, ReviewedPrehistoryArchive

PrehistoryPackage = ReviewedPrehistoryArchive | CompiledPrehistoryArchive


def configured_prehistory(*, path: Path | None, supplied: PrehistoryPackage | None,
                         world_id: str, actor_ref: str) -> PrehistoryPackage | None:
    if path is None:
        archive = supplied
    else:
        with Path(path).open("rb") as stream:
            raw = stream.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("prehistory package exceeds deployment size limit")
        archive = TypeAdapter(PrehistoryPackage).validate_json(raw)
        if supplied is not None and supplied != archive:
            raise ValueError("explicit and configured prehistory packages disagree")
    if archive is not None and (
        archive.document.world_id != world_id or archive.document.actor_ref != actor_ref
    ):
        raise ValueError("configured prehistory belongs to another World or actor")
    return archive
