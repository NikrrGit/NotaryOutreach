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

    class EvoluationResult(BaseModel):
        """
        Structured result returned by the evaluator
        """

        passed : bool 

        appointment_request : bool
        correct_company_type : bool
        claims_supported : bool

        score : float = Field(ge=0.0, le=1.0)

        issues: list[str] = Field(defualt_factory=list)


    class EvaluatorInput(BaseModel):
        """
            Everything the evaluator needs to compare the generated
            email against the verified source information.
        """

        company_type: str

        notary_name: str
        city: str

        verification_reason: str
        evidence: str
        source_url: str

        email_subject: str
        email_body: str

    class EmailEvaluator:
        """
        Evaluates an email draft before it reaches human review.

        Checks:
            1. Does the email request an appointment?
            2. Does it use the correct company type?
            3. Are factual claims supported by verified evidence?

        NOT responsible for:
            - Discovering notaries.
            - Verifying notaries.
            - Rewriting emails.
            - Sending emails.
         """

    def __init__(
        self,
        provider: EvaluatorProvider,
        pass_threshold: float = 0.85,
    ) -> None:
        self.provider = provider
        self.pass_threshold = pass_threshold

    def evaluate(self, data: EvaluatorInput) -> EvoluationResult:
        """
        Evaluate one generate email draft
        """

        result = self.provider.generate_structured(
            system_prompt =self._system_prompt(),
            user_prompt=self._build_prompt(data),
            response_model= self.EvoluationResult,
        )

        if not isinstance(result, self.EvoluationResult):
            result = self.EvoluationResult.model_validate(result)

        return self._apply_pass_rules(result)