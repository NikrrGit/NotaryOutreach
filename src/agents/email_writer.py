from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from .discovery import Candidate
from .verifier import VerificationResult


class EmailWriterProvider(Protocol):
    """Minimal provider contract required by the email writer."""

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        ...


class EmailDraft(BaseModel):
    """Structured email produced for a verified notary."""

    subject: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=2000)
    language: str = "de"
    personalisation_used: str | None = None


class EmailWriterInput(BaseModel):
    """Information supplied by the caller after verification."""

    notary_name: str
    city: str
    email: str | None = None
    company_type: str
    verification_reason: str
    evidence: str
    source_url: str
    sender_name: str | None = None
    company_name: str | None = None

    @classmethod
    def from_verification(
        cls,
        verification: VerificationResult,
        *,
        sender_name: str | None = None,
        company_name: str | None = None,
    ) -> EmailWriterInput:
        """Map a supported verifier result into drafting input.

        The verifier owns evidence matching and the confidence threshold.
        Discovery contact details are carried through, not reverified here.
        """
        if not isinstance(verification, VerificationResult):
            raise TypeError("verification must be a VerificationResult.")
        if verification.status != "supported":
            raise ValueError("Cannot generate email unless verification is supported.")
        if not verification.reasoning.strip():
            raise ValueError("Cannot generate email without a verification reason.")
        if not verification.evidence_quote or not verification.evidence_quote.strip():
            raise ValueError("Cannot generate email without source evidence.")
        if not verification.source_url or not verification.source_url.strip():
            raise ValueError("Cannot generate email without an evidence source URL.")
        Candidate.validate_url(verification.source_url)
        return cls(
            notary_name=verification.candidate.name,
            city=verification.candidate.city,
            email=verification.candidate.email,
            company_type=verification.company_type,
            verification_reason=verification.reasoning,
            evidence=verification.evidence_quote,
            source_url=verification.source_url,
            sender_name=sender_name,
            company_name=company_name,
        )


class EmailWriter:
    """Generate German appointment-request drafts from verified information.

    This agent writes drafts for human approval; it does not discover or
    verify notaries, send emails, or decide whether drafts are approved.
    """

    def __init__(self, provider: EmailWriterProvider) -> None:
        self.provider = provider

    def write_verified(
        self,
        verification: VerificationResult,
        *,
        sender_name: str | None = None,
        company_name: str | None = None,
    ) -> EmailDraft:
        """Draft for a supported result; reject ineligible results before calling AI."""
        data = EmailWriterInput.from_verification(
            verification, sender_name=sender_name, company_name=company_name,
        )
        return self.writer(data)

    def writer(self, data: EmailWriterInput) -> EmailDraft:
        """Generate one email draft from verified candidate information."""
        if not data.verification_reason.strip():
            raise ValueError("Cannot generate email without a verification reason")
        if not data.evidence.strip():
            raise ValueError("Cannot generate email without source evidence")

        result = self.provider.generate_structured(
            system_prompt=self._system_prompt(),
            user_prompt=self._build_prompt(data),
            response_model=EmailDraft,
        )
        if not isinstance(result, EmailDraft):
            result = EmailDraft.model_validate(result)
        return result

    @staticmethod
    def _system_prompt() -> str:
        return """
You write professional appointment-request emails to German notaries.

Your task is ONLY to write the email.

Rules:

1. Write in natural, professional German.
2. Keep the email concise: approximately 60-120 words.
3. State clearly that the sender wants to establish the requested
   company type.
4. Ask for the earliest possible notary appointment.
5. Personalise only using facts explicitly provided in the input.
6. Never invent services, expertise, availability, titles or names.
7. Do not exaggerate the notary's expertise.
8. Do not use marketing language or unnecessary compliments.
9. Do not claim that an appointment is available.
10. Do not include facts that cannot be supported by the supplied
    verification evidence.
11. The email is a draft for human approval. Do not imply that it
    has already been sent.
12. Use a neutral greeting if the recipient's correct personal title
    or gender cannot be established safely.

The objective is clarity and factual correctness, not creativity.
""".strip()

    @staticmethod
    def _build_prompt(data: EmailWriterInput) -> str:
        sender = data.sender_name or "Not provided"
        company = data.company_name or "Not provided"

        return f"""
Create an appointment-request email using ONLY the information below.

NOTARY

Name:
{data.notary_name}

City:
{data.city}

Email:
{data.email or "Not available"}

VERIFIED INFORMATION

Verification reason:
{data.verification_reason}

Evidence:
{data.evidence}

Evidence source:
{data.source_url}

REQUEST

Company type:
{data.company_type}

The sender wants the earliest possible appointment for establishing
a {data.company_type} in Germany.

SENDER

Name:
{sender}

Company/startup name:
{company}

REQUIREMENTS

- Write the subject and email body.
- Language: German.
- Approximately 60-120 words.
- Ask explicitly for the earliest available appointment.
- Mention the verified service only if it improves the email naturally.
- Do not invent anything that is not contained above.
- Do not add a fake sender name when none was supplied.
""".strip()


# Preserve the original class name for existing callers.
EmailWritter = EmailWriter
