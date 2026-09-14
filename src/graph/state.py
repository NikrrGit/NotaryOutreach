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
    langauge: str = Field(default='de')


# Error state

class WorkFlow

