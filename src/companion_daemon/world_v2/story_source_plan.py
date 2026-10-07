"""Lossless, bounded manuscript partitions; never infer scene meaning."""
from __future__ import annotations

import hashlib
from typing import Literal
from pydantic import Field, model_validator
from .schema_core import FrozenModel


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class ManuscriptChapter(FrozenModel):
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    text: str = Field(min_length=1)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def exact_span(self):
        if self.end_char - self.start_char != len(self.text) or text_hash(self.text) != self.sha256:
            raise ValueError('manuscript chapter changed its source span')
        return self


class ManuscriptPlan(FrozenModel):
    contract: Literal['story-manuscript-plan.1'] = 'story-manuscript-plan.1'
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_characters: int = Field(gt=0)
    chapter_bytes_limit: int = Field(ge=256, le=120000)
    chapters: tuple[ManuscriptChapter, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def coverage_is_complete(self):
        cursor = 0
        for chapter in self.chapters:
            if chapter.start_char != cursor or len(chapter.text.encode('utf-8')) > self.chapter_bytes_limit:
                raise ValueError('manuscript coverage has a gap, overlap or oversized chapter')
            cursor = chapter.end_char
        if cursor != self.source_characters or text_hash(''.join(c.text for c in self.chapters)) != self.source_sha256:
            raise ValueError('manuscript coverage differs from its complete source')
        return self


def plan_manuscript(text: str, *, chapter_bytes_limit: int = 24000) -> ManuscriptPlan:
    if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 4_000_000:
        raise ValueError('manuscript must be nonblank and bounded to four million bytes')
    if type(chapter_bytes_limit) is not int or not 256 <= chapter_bytes_limit <= 120000:
        raise ValueError('invalid manuscript chapter byte limit')
    chapters = []
    start = size = 0
    last_newline = None
    index = 0
    while index < len(text):
        width = len(text[index].encode('utf-8'))
        if size + width > chapter_bytes_limit:
            end = last_newline if last_newline is not None and last_newline > start else index
            part = text[start:end]
            chapters.append(ManuscriptChapter(start_char=start, end_char=end, text=part, sha256=text_hash(part)))
            start = index = end
            size = 0
            last_newline = None
            continue
        size += width
        index += 1
        if text[index-1] == '\n':
            last_newline = index
    if start < len(text):
        part = text[start:]
        chapters.append(ManuscriptChapter(start_char=start, end_char=len(text), text=part, sha256=text_hash(part)))
    return ManuscriptPlan(source_sha256=text_hash(text), source_characters=len(text),
                          chapter_bytes_limit=chapter_bytes_limit, chapters=tuple(chapters))
