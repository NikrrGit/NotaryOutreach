from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class EvaluatorProvider(Protocol):
    """
    Minimal provider capability required by the evaluator.

    The evaluator depends on an interface rather than directly
    depending on Groq.
    """

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        ...

    class EvolutionResult(BaseModel):
        """
        Structured result returned by the evaluator
        """

        passed : bool 

        appointment_request : bool
        correct_company_type : bool
        claims_supported : bool

        score : float = Field(ge=0.0, le=1.0)

        issues: list[str] = Field(defualt_factory=list)



