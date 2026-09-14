from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

from src.models.candidate import Candidate
from src.models.verification import VerificationResult
from src.models.email import EmailDraft
from src.models.evaluation import EvaluationResult


CompanyType = Literal["UG", "GmbH"]
