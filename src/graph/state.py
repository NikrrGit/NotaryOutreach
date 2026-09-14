from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

from src.models.candidate import Candidate
from src.models.verification import VerificationResult
from src.models.email import EmailDraft
from src.models.evaluation import EvaluationResult


CompanyType = Literal["UG", "GmbH"]

# Workflwo outpot

class SearchSettings(BaseModel):
    """
    User provide configuration for any notary search job
    """

    location:str = Field(min_length=1)
    Company_type : CompanyType
    target_count : int = Field(default=20, ge=1,le=100)

    radius_km : int = Field(default=50, ge=1, le=200)
    language: str = Field(default='de')


# Error state

class WorkFlow(BaseModel):
    """
    Structured error recorded during workflow execution.

    Errors are stored in state instead of immediately terminating
    the entire workflow.
    """

    node: str
    message: str

    candidate_id : str | None =  None 
    retryable: bool = False


# Retry State

class RetryCounts(TypedDict):
    """
    Number of retries already performed by each workflow stage.
    """

    discovery: int
    verification : int
    email_generation: int
    evaluation : int


# Main Langrgapgh state

class WorkflowState(TypedDict):
    """
    Shared state passed between LangGraph nodes.

    Every node receives this state and returns ONLY the fields
    it wants to update.
    """
    # Search config 
    settings: SearchSettings

    # Discovery 
    candidates = Annotated[
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