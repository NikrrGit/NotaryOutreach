from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from agents.discovery import Candidate
from agents.verifier import VerificationResult


CompanyType = Literal["UG", "GmbH"]

# Search configuration

class SearchSettings(BaseModel):
    """
    User-provided configuration for a notary search job.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    location: str = Field(min_length=1)
    company_type: CompanyType
    target_count: int = Field(default=20, ge=1, le=100, strict=True)

    radius_km: int = Field(default=50, ge=1, le=200, strict=True)
    language: str = Field(default="de", min_length=1)


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

    # Errors
    errors: Annotated[
        list[WorkflowError],
        operator.add,
    ]

    # Retry Tracking
    retry_counts: RetryCounts
