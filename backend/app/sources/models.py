"""Typed representation of a retrieved source."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, HttpUrl


class SourceKind(str, Enum):
    ARXIV = "arxiv"
    WIKIPEDIA = "wikipedia"


# Prior reliability by kind, used later by the conflict resolver. Peer-review
# status is unknown for arXiv preprints, so they sit between an encyclopaedia
# summary and a published trial.
AUTHORITY: dict[SourceKind, float] = {SourceKind.ARXIV: 0.75, SourceKind.WIKIPEDIA: 0.60}


class Source(BaseModel):
    source_id: str = Field(min_length=3, max_length=120)
    kind: SourceKind
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    authors: list[str] = Field(default_factory=list)
    published_year: int | None = Field(default=None, ge=1900, le=2100)
    summary: str = Field(default="", max_length=6000)
    authority: float = Field(ge=0, le=1)
    sub_question_index: int | None = None

    @property
    def citation_label(self) -> str:
        first = self.authors[0].split()[-1] if self.authors else self.kind.value
        year = f" {self.published_year}" if self.published_year else ""
        return f"{first}{year}"
