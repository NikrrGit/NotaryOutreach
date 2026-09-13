"""Verify company formation services against an office's published website text."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Literal
from urllib.parse import urlsplit

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

    def _read_pages(
        self, candidate: Candidate,
    ) -> tuple[list[PageContent], list[VerificationFailure]]:
        """Read the homepage and same-office sources, retaining individual errors."""
        pages: list[PageContent] = []
        errors: list[VerificationFailure] = []
        if not candidate.website:
            return pages, [VerificationFailure(stage="fetch", message="No official website supplied.")]

        official_host = urlsplit(candidate.website).hostname.casefold().removeprefix("www.").rstrip(".")
        pending = [candidate.website, candidate.source_url]
        visited: set[str] = set()
        attempts = 0
        while pending and attempts < self.max_pages:
            raw_url = pending.pop(0)
            try:
                Candidate.validate_url(raw_url)
                parsed = urlsplit(raw_url)
                host = parsed.hostname.casefold().removeprefix("www.").rstrip(".")
                if host != official_host:
                    continue
                url = parsed._replace(fragment="").geturl()
                key = parsed._replace(fragment="", path=parsed.path.rstrip("/")).geturl()
            except (ValueError, AttributeError, TypeError):
                continue
            if key in visited:
                continue
            visited.add(key)
            attempts += 1
            links: list[str] = []
            try:
                text = self.page_reader(url, links)
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("No readable text.")
                pages.append(PageContent(source_url=url, text=text.strip()))
            except Exception as exc:
                errors.append(VerificationFailure(
                    stage="fetch", source_url=url,
                    message=f"Could not read page ({type(exc).__name__}).",
                ))
            pending.extend(links)
        return pages, errors
