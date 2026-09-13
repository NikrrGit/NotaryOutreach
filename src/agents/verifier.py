"""Verify company formation services against an office's published website text."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from notaryoutreach.providers.groq import assess_formation
from notaryoutreach.verification import fetch_page_text

from .discovery import Candidate

CompanyType = Literal["UG", "GmbH"]
AssessmentProvider = Callable[[str, str, dict], str]
PageReader = Callable[[str, list[str]], str]


class Assessment(BaseModel):
    """A model assessment, checked against fetched evidence before returning it."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    status: Literal["supported", "unsupported", "unknown"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    reasoning: str = Field(min_length=1)
    evidence_quote: str | None
    source_url: str | None


class PageContent(BaseModel):
    source_url: str
    text: str


class VerificationFailure(BaseModel):
    stage: Literal["fetch", "assessment", "verification"]
    source_url: str | None = None
    message: str


class VerificationResult(Assessment):
    candidate: Candidate
    company_type: CompanyType
    pages_reviewed: list[str] = Field(default_factory=list)
    errors: list[VerificationFailure] = Field(default_factory=list)


class VerificationAgent:
    """Read official pages and retain inconclusive or failed checks as unknown."""

    def __init__(
        self,
        *,
        provider: AssessmentProvider | None = None,
        page_reader: PageReader | None = None,
        client=None,
        env_file: str = ".env",
        max_pages: int = 4,
        minimum_confidence: float = 0.8,
    ) -> None:
        if type(max_pages) is not int or not 1 <= max_pages <= 10:
            raise ValueError("max_pages must be an integer between 1 and 10.")
        if type(minimum_confidence) not in (int, float) or not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1.")
        if provider is not None and client is not None:
            raise ValueError("Supply either a provider or a client.")
        self.provider = provider if provider is not None else partial(
            assess_formation, client=client, env_file=env_file,
        )
        self.page_reader = page_reader if page_reader is not None else fetch_page_text
        self.max_pages = max_pages
        self.minimum_confidence = float(minimum_confidence)
