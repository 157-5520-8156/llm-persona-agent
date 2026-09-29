"""Explicit deployment of reviewed, owner-bound fictional backstory."""
from pathlib import Path

from .character_prehistory import ReviewedPrehistoryArchive


def configured_prehistory(*, path: Path | None, supplied: ReviewedPrehistoryArchive | None,
                         world_id: str, actor_ref: str) -> ReviewedPrehistoryArchive | None:
    if path is None:
        archive = supplied
    else:
        with Path(path).open("rb") as stream:
            raw = stream.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("reviewed prehistory package exceeds deployment size limit")
        archive = ReviewedPrehistoryArchive.model_validate_json(raw)
        if supplied is not None and supplied != archive:
            raise ValueError("explicit and configured prehistory packages disagree")
    if archive is not None and (
        archive.document.world_id != world_id or archive.document.actor_ref != actor_ref
    ):
        raise ValueError("configured prehistory belongs to another World or actor")
    return archive
