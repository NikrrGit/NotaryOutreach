from __future__ import annotations

import operator
from typing import Annotated, Literal, NotRequired, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agents.discovery import Candidate, OutreachContext
from agents.email_writer import EmailDraft
from agents.verification import VerificationResult


CompanyType = Literal["UG", "GmbH"]

# Search configuration

class SearchSettings(OutreachContext):
    """Search settings for the shared Notary/VC workflow."""

    model_config = ConfigDict(str_strip_whitespace=True)

    location: str = Field(min_length=1)
    target_count: int = Field(default=20, ge=1, le=100, strict=True)

    radius_km: int = Field(default=50, ge=1, le=200, strict=True)
    language: str = Field(default="de", min_length=1)

    def agent_context(self) -> dict:
        """Return only the shared agent input fields."""
        return self.model_dump(include=set(OutreachContext.model_fields))


# Draft and evaluation state

class CandidateEmailDraft(BaseModel):
    """A draft linked to its candidate, with an ID for evaluation references.

    Store each regenerated draft as a new record so evaluations remain tied
    to the exact version they reviewed.
    """

    draft_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    candidate: Candidate
    draft: EmailDraft


class EvaluationResult(BaseModel):
    """Quality assessment of a stored draft, not human approval to send it.

    draft_id references CandidateEmailDraft.draft_id, which also identifies
    the associated candidate. The workflow must enforce that reference.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    draft_id: str = Field(min_length=1)
    passed: bool = Field(strict=True)
    reasoning: str = Field(min_length=1)
    issues: list[str] = Field(default_factory=list)


# Error state

class WorkflowError(BaseModel):
    """
    Structured error recorded during workflow execution.

    Errors are stored in state instead of immediately terminating
    the entire workflow.
    """

    node: str
    message: str

    candidate_id: str | None = None
    retryable: bool = False


# Retry State

class RetryCounts(TypedDict):
    """
    Number of retries already performed by each workflow stage.
    """

    discovery: int
    verification: int
    email_generation: int
    evaluation: int


# Main LangGraph state

class WorkflowState(TypedDict):
    """
    Shared state passed between LangGraph nodes.

    Every node receives this state and returns ONLY the fields
    it wants to update.
    """
    # Search config
    settings: SearchSettings

    # Discovery
    candidates: Annotated[
        list[Candidate],
        operator.add,
    ]

    # Verification
    verification_results: Annotated[
        list[VerificationResult],
        operator.add,
    ]

    # Draft history: append a new record for each generated version.
    email_drafts: Annotated[
        list[CandidateEmailDraft],
        operator.add,
    ]

    # Evaluations reference a specific draft version and its candidate.
    evaluations: Annotated[
        list[EvaluationResult],
        operator.add,
    ]

    # Errors
    errors: Annotated[
        list[WorkflowError],
        operator.add,
    ]

    # Retry Tracking
    retry_counts: RetryCounts
    status: NotRequired[Literal["pending", "manual_review", "ready_for_review"]]
